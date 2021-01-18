import os
from uuid import uuid4
from enum import Enum
from datetime import date

from pydantic import BaseModel, ValidationError
from flask import Flask, request, jsonify
from werkzeug.exceptions import BadRequest
import sqlalchemy as sa
import requests

import database
import circuit_breaker as cb

app = Flask(__name__)
app.url_map.strict_slashes = False
ROOT_PATH = "/api/v1"
WAREHOUSE_SERVICE_URL = os.environ.get("WAREHOUSE_SERVICE_URL", "localhost:8280")
print(f"Warehouse service url: {WAREHOUSE_SERVICE_URL} ($WAREHOUSE_SERVICE_URL)")
WARRANTY_SERVICE_URL = os.environ.get("WARRANTY_SERVICE_URL", "localhost:8180")
print(f"Warranty service url: {WARRANTY_SERVICE_URL} ($WARRANTY_SERVICE_URL)")

circuit_breaker = cb.CircuitBreaker()

# ------------------------------ dto ------------------------------


class Order(database.Base):
    __tablename__ = 'orders'
    id = sa.Column(sa.Integer, primary_key=True)
    item_uid = sa.Column(sa.Text)
    order_date = sa.Column(sa.TIMESTAMP)
    order_uid = sa.Column(sa.Text, unique=True)
    status = sa.Column(sa.VARCHAR(255))
    user_uid = sa.Column(sa.Text)


class NewOrderRequest(BaseModel):
    model: str
    size: str


class WarrantyRequest(BaseModel):
    reason: str


class Status(str, Enum):
    paid = "PAID"
    canceled = "CANCELED"
    waiting = "WAITING"

# ------------------------------ вспомогательные функции ------------------------------


@app.errorhandler(Exception)
def default_error_handler(error):
    return {
        "message": f"An error occurred: {repr(error)}"
    }, 500

# ------------------------------ методы api ------------------------------


@app.route("/manage/health", methods=["GET"])
def health_check():
    return "UP", 200


@app.route(f"{ROOT_PATH}/orders/<string:user_uid>", methods=["POST"])
@cb.handles_circuit_break
def request_new_order(user_uid):
    """
    Сделать заказ от имени пользователя
    """
    # парсим входные данные
    try:
        new_item_request = NewOrderRequest.parse_obj(request.get_json(force=True))
    except BadRequest:
        return {"message": "Bad json"}, 400
    except ValidationError as e:
        return {"message": e.errors()}, 400

    order_uid = str(uuid4())

    # запрашиваем новый item из warehouse
    warehouse_service_response = circuit_breaker.external_request(
        "POST",
        f"http://{WAREHOUSE_SERVICE_URL}{ROOT_PATH}/warehouse",
        json={
            "orderUid": order_uid,
            "model": new_item_request.model,
            "size": new_item_request.size
        }
    )
    if not warehouse_service_response.ok:
        return {"message": f"bad response from warehouse "
                           f"({warehouse_service_response.status_code}): "
                           f"{warehouse_service_response.text}"}, 422
    elif not warehouse_service_response.json().get("orderItemUid"):
        return {"message": "Something terrible happens to warehouse :/"}, 500
    item_uid = warehouse_service_response.json().get("orderItemUid")

    # создаем гарантию в warranty service
    try:
        circuit_breaker.external_request(
            "POST",
            f"http://{WARRANTY_SERVICE_URL}{ROOT_PATH}/warranty/{item_uid}"
        )
    except cb.CircuitBreakerException:
        # Откат, если недоступен warranty service, в базу ничего сохранено не будет
        requests.delete(f"http://{WAREHOUSE_SERVICE_URL}{ROOT_PATH}/warehouse/{item_uid}")
        return "Warranty service suddenly became unavailable, rolling back", 502

    # сохраняем в базу
    with database.Session() as s:
        s.add(Order(
            item_uid=warehouse_service_response.json()["orderItemUid"],
            order_date=date.today(),
            order_uid=order_uid,
            status=Status.paid,
            user_uid=user_uid,
        ))

    return {"orderUid": order_uid}, 200


@app.route(f"{ROOT_PATH}/orders/<string:user_uid>/<string:order_uid>", methods=["GET"])
def request_order(user_uid, order_uid):
    """
    Получить информацию по конкретному заказу пользователя
    """
    # просто достаем order из базы
    with database.Session() as s:
        order = (
            s.query(Order)
            .filter(Order.order_uid == order_uid)
            .filter(Order.user_uid == user_uid)
            .one_or_none()
        )
        if not order:
            return {"message": "Not found"}, 404

        return {
            "orderUid": order.order_uid,
            "orderDate": order.order_date.isoformat(),
            "itemUid": order.item_uid,
            "status": order.status
        }, 200


@app.route(f"{ROOT_PATH}/orders/<string:user_uid>", methods=["GET"])
def request_all_orders(user_uid):
    """
    Получить все заказы пользователя
    """
    # просто достаем order'ы из базы
    with database.Session() as s:
        orders = s.query(Order).filter(Order.user_uid == user_uid).all()

        result = [{
            "orderUid": order.order_uid,
            "orderDate": order.order_date.isoformat(),
            "itemUid": order.item_uid,
            "status": order.status
        } for order in orders]
        return jsonify(result), 200


@app.route(f"{ROOT_PATH}/orders/<string:order_uid>/warranty", methods=["POST"])
@cb.handles_circuit_break
def request_warranty(order_uid):
    """
    Запрос гарантии по заказу
    """
    # парсим входные данные
    try:
        warranty_request = WarrantyRequest.parse_obj(request.get_json(force=True))
    except BadRequest:
        return {"message": "Bad json"}, 400
    except ValidationError as e:
        return {"message": e.errors()}, 400

    with database.Session() as s:
        # убеждаемся, что заказ есть в базе
        order = s.query(Order).filter(Order.order_uid == order_uid).one_or_none()
        if not order:
            return {"message": "Order not found"}, 404

        # перенаправляем запрос на warehouse
        warehouse_service_response = circuit_breaker.external_request(
            "POST",
            f"http://{WAREHOUSE_SERVICE_URL}{ROOT_PATH}/warehouse/{order.item_uid}/warranty",
            json={"reason": warranty_request.reason}
        )
        if not warehouse_service_response.ok:
            return {"message": "Warranty not found"}, 404

    return warehouse_service_response.json(), 200


@app.route(f"{ROOT_PATH}/orders/<string:order_uid>", methods=["DELETE"])
@cb.handles_circuit_break
def request_delete_order(order_uid):
    """
    Вернуть заказ
    """
    with database.Session() as s:
        # достаем заказ из базы
        order = s.query(Order).filter(Order.order_uid == order_uid).one_or_none()
        if not order:
            return {"message": "Order not found"}, 404

        # запрашиваем в warehouse возврат
        warehouse_service_response = circuit_breaker.external_request(
            "DELETE",
            f"http://{WAREHOUSE_SERVICE_URL}{ROOT_PATH}/warehouse/{order.item_uid}",
        )
        if not warehouse_service_response.ok:
            return {"message": "Order not found on warehouse"}, 422

        # удаляем из базы
        s.delete(order)
    return '', 204


if __name__ == '__main__':
    PORT = os.environ.get("PORT", 8380)
    print("LISTENING ON PORT:", PORT, "($PORT)")
    database.create_schema()
    app.run("0.0.0.0", PORT)
