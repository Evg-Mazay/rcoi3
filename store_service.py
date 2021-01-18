# Сделал ЛР №3
# См. в этом файле комментарии, начинаеющиеся с '# ЛР3'
# Circuit Breaker см. в файле circuit_breaker.py
# Circuit Breaker поставлен в каждом сервисе на каждом методе api, который куда-то обращается
#

import os
from datetime import datetime

from pydantic import BaseModel, ValidationError
from flask import Flask, request, jsonify
from werkzeug.exceptions import BadRequest
import sqlalchemy as sa

import database
import circuit_breaker as cb
import rabbitmq as mq

app = Flask(__name__)
app.url_map.strict_slashes = False
ROOT_PATH = "/api/v1"
ORDER_SERVICE_URL = os.environ.get("ORDER_SERVICE_URL", "localhost:8380")
print(f"Order service url: {ORDER_SERVICE_URL} ($ORDER_SERVICE_URL)")
WAREHOUSE_SERVICE_URL = os.environ.get("WAREHOUSE_SERVICE_URL", "localhost:8280")
print(f"Warehouse service url: {WAREHOUSE_SERVICE_URL} ($WAREHOUSE_SERVICE_URL)")
WARRANTY_SERVICE_URL = os.environ.get("WARRANTY_SERVICE_URL", "localhost:8180")
print(f"Warranty service url: {WARRANTY_SERVICE_URL} ($WARRANTY_SERVICE_URL)")

circuit_breaker = cb.CircuitBreaker()

# ------------------------------ dto ------------------------------


class User(database.Base):
    __tablename__ = 'users'
    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.Text, unique=True)
    user_uid = sa.Column(sa.Text, unique=True)


class WarrantyRequest(BaseModel):
    reason: str


class NewOrderRequest(BaseModel):
    model: str
    size: str

# ------------------------------ вспомогательные функции ------------------------------


def refresh_items_in_db():
    with database.Session() as s:
        s.execute(User.__table__.delete())
        s.add_all([
            User(id=1, name="Alex", user_uid="6d2cb5a0-943c-4b96-9aa6-89eac7bdfd2b"),
        ])
        print("Initialized default values in User table")


def is_user_exists(user_uid):
    with database.Session() as s:
        user = s.query(User).filter(User.user_uid == user_uid).one_or_none()
        return bool(user)


# ЛР3 1: Возврат ошибок в json
@app.errorhandler(Exception)
def default_error_handler(error):
    return {
        "message": f"An error occurred: {repr(error)}"
    }, 500

# ------------------------------ методы api ------------------------------


@app.route("/manage/health", methods=["GET"])
def health_check():
    return "UP", 200


@app.route(f"{ROOT_PATH}/store/<string:user_uid>/orders", methods=["GET"])
@cb.handles_circuit_break
def request_all_orders(user_uid):
    """
    Получить список заказов пользователя
    """
    user_uid = user_uid.lower()
    if not is_user_exists(user_uid):
        return {"message": "User not found"}, 404

    # запрос заказов юзера из order_service
    order_service_response = circuit_breaker.external_request(
        "GET",
        f"http://{ORDER_SERVICE_URL}{ROOT_PATH}/orders/{user_uid}"
    )
    if not order_service_response.ok:
        return {"message": "Order not found"}, 422

    result = []

    # для каждого заказа, который сделал юзер
    for order in order_service_response.json():
        order_uid = order["orderUid"]
        item_uid = order["itemUid"]

        # запросить инфу из warehouse
        try:
            warehouse_service_response = circuit_breaker.external_request(
                "GET",
                f"http://{WAREHOUSE_SERVICE_URL}{ROOT_PATH}/warehouse/{item_uid}"
            )
            if not warehouse_service_response.ok:
                return {"message": "Order in warehouse not found"}, 422
        except cb.CircuitBreakerException:
            warehouse_service_response = None

        # запросить инфу из warranty
        try:
            warranty_service_response = circuit_breaker.external_request(
                "GET",
                f"http://{WARRANTY_SERVICE_URL}{ROOT_PATH}/warranty/{item_uid}"
            )
            if not warranty_service_response.ok:
                return {"message": "Warranty not found"}, 422
        except cb.CircuitBreakerException:
            warranty_service_response = None

        # ЛР3 4a: Собираем результат из того, что доступно
        order_info = {
            "orderUid": order_uid,
            "date": order["orderDate"]
        }
        if warehouse_service_response:
            order_info.update({
                "model": warehouse_service_response.json()["model"],
                "size": warehouse_service_response.json()["size"]
            })
        if warranty_service_response:
            order_info.update({
                "warrantyDate": warranty_service_response.json()["warrantyDate"],
                "warrantyStatus": warranty_service_response.json()["status"]
            })
        if not warehouse_service_response or not warranty_service_response:
            order_info["circuit_breaker"] = "Some services unavailable, information is not complete"

        result.append(order_info)

    return jsonify(result), 200


@app.route(f"{ROOT_PATH}/store/<string:user_uid>/<string:order_uid>", methods=["GET"])
@cb.handles_circuit_break
def request_order(user_uid, order_uid):
    """
    Информация по конкретному заказу
    """
    user_uid = user_uid.lower()
    order_uid = order_uid.lower()
    if not is_user_exists(user_uid):
        return {"message": "User not found"}, 404

    # запрос заказа из order_service
    order_service_response = circuit_breaker.external_request(
        "GET",
        f"http://{ORDER_SERVICE_URL}{ROOT_PATH}/orders/{user_uid}/{order_uid}"
    )
    if not order_service_response.ok:
        return {"message": "Order not found"}, 422
    item_uid = order_service_response.json()["itemUid"]

    # для этого заказа загружаем инфу из warehouse
    try:
        warehouse_service_response = circuit_breaker.external_request(
            "GET",
            f"http://{WAREHOUSE_SERVICE_URL}{ROOT_PATH}/warehouse/{item_uid}"
        )
        if not warehouse_service_response.ok:
            return {"message": "Order in warehouse not found"}, 422
    except cb.CircuitBreakerException:
        warehouse_service_response = None

    # а также инфу из warranty
    try:
        warranty_service_response = circuit_breaker.external_request(
            "GET",
            f"http://{WARRANTY_SERVICE_URL}{ROOT_PATH}/warranty/{item_uid}"
        )
        if not warranty_service_response.ok:
            return {"message": "Warranty not found"}, 422
    except cb.CircuitBreakerException:
        warranty_service_response = None

    # ЛР3 4a: Собираем результат из того, что доступно
    result = {
        "orderUid": order_uid,
        "date": order_service_response.json()["orderDate"],
    }
    if warehouse_service_response:
        result.update({
            "model": warehouse_service_response.json()["model"],
            "size": warehouse_service_response.json()["size"]
        })
    if warranty_service_response:
        result.update({
            "warrantyDate": warranty_service_response.json()["warrantyDate"],
            "warrantyStatus": warranty_service_response.json()["status"]
        })
    if not warehouse_service_response or not warranty_service_response:
        result["circuit_breaker"] = "Some services unavailable, information is not complete"

    return result, 200


@app.route(f"{ROOT_PATH}/store/<string:user_uid>/<string:order_uid>/warranty", methods=["POST"])
def request_warranty(user_uid, order_uid):
    """
    Запрос гарантии по заказу
    """
    user_uid = user_uid.lower()
    order_uid = order_uid.lower()
    if not is_user_exists(user_uid):
        return {"message": "User not found"}, 404

    # парсим входные данные
    try:
        warranty_request = WarrantyRequest.parse_obj(request.get_json(force=True))
    # ЛР3 2: Возвращать 400 на некорректные входные данные
    except BadRequest:
        return {"message": "Bad json"}, 400
    except ValidationError as e:
        return {"message": e.errors()}, 400

    # перенаправляем запрос в order_service
    try:
        order_service_response = circuit_breaker.external_request(
            "POST",
            f"http://{ORDER_SERVICE_URL}{ROOT_PATH}/orders/{order_uid}/warranty",
            json={"reason": warranty_request.reason}
        )
    except cb.CircuitBreakerException:
        # ЛР3 4c: Ставим в очередь запрос, если система недоступна
        with mq.Queue() as q:
            q.publish({"time": str(datetime.utcnow()), "reason": warranty_request.reason})
        return {"message": "Warranty service unavailable, but SUCCESS! Your request added to queue"}, 200

    if not order_service_response.ok:
        return {"message": "Order not found"}, 422

    # ЛР3 4c: Читаем то, что сохранено в очереди и пытаемся выполнить
    queued_requests = []
    try:
        with mq.Queue() as q:
            for req in q.consume():
                resp = circuit_breaker.external_request(
                    "POST",
                    f"http://{ORDER_SERVICE_URL}{ROOT_PATH}/orders/{order_uid}/warranty",
                    json={"reason": req["reason"]}
                )
                queued_requests.append({"time": req["time"], "result": resp.json()})
    except cb.CircuitBreakerException:
        pass

    result = {"orderUid": order_uid, **order_service_response.json()}
    # если что-то из очереди выполнилось успешно, возвращаем это в атрибуте queued_requests
    if queued_requests:
        result["queued_requests"] = queued_requests
    return result, 200


@app.route(f"{ROOT_PATH}/store/<string:user_uid>/purchase", methods=["POST"])
@cb.handles_circuit_break
def request_purchase(user_uid):
    """
    Выполнить покупку
    """
    user_uid = user_uid.lower()
    if not is_user_exists(user_uid):
        return {"message": "User not found"}, 404

    # парсим входные данные
    try:
        new_order_request = NewOrderRequest.parse_obj(request.get_json(force=True))
    # ЛР3 2: Возвращать 400 на некорректные входные данные
    except BadRequest:
        return {"message": "Bad json"}, 400
    except ValidationError as e:
        return {"message": e.errors()}, 400

    # перенаправляем запрос в order_service
    order_service_response = circuit_breaker.external_request(
        "POST",
        f"http://{ORDER_SERVICE_URL}{ROOT_PATH}/orders/{user_uid}",
        json={"model": new_order_request.model, "size": new_order_request.size}
    )
    # ЛР3 4b: Откат операции при недоступности системы (в остальных сервисах тоже поддерживается)
    if not order_service_response.ok:
        return {"message": "Order not created due to errors. All changes was rolled back"}, 422

    order_uid = order_service_response.json()["orderUid"]
    return '', 201, {"Location": f"{ROOT_PATH}/store/{user_uid}/{order_uid}"}


@app.route(f"{ROOT_PATH}/store/<string:user_uid>/<string:order_uid>/refund", methods=["DELETE"])
@cb.handles_circuit_break
def request_refund(user_uid, order_uid):
    """
    Вернуть заказ
    """
    user_uid = user_uid.lower()
    order_uid = order_uid.lower()
    if not is_user_exists(user_uid):
        return {"message": "User not found"}, 404

    # перенаправляем запрос в order_service
    order_service_response = circuit_breaker.external_request(
        "DELETE",
        f"http://{ORDER_SERVICE_URL}{ROOT_PATH}/orders/{order_uid}"
    )
    # ЛР3 4b: Откат операции при недоступности системы (в остальных сервисах тоже поддерживается)
    if not order_service_response.ok:
        return {"message": "Order not refunded due to errors. All changes was rolled back"}, 422
    return '', 204


if __name__ == '__main__':
    PORT = os.environ.get("PORT", 8480)
    print("LISTENING ON PORT:", PORT, "($PORT)")
    database.create_schema()
    refresh_items_in_db()
    app.run("0.0.0.0", PORT)
