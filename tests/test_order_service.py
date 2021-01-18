from database import Session
from order_service import Order
from datetime import date
import re

import requests_mock
import pytest

from order_service import app


@pytest.fixture()
def add_some_order():
    with Session() as s:
        s.add(Order(
            item_uid="item-1",
            order_date=date.today(),
            order_uid="1-1-1",
            status="PAID",
            user_uid="1",
        ))


def test_request_new_order(fresh_database):
    with app.test_client() as test_client:
        with requests_mock.Mocker(real_http=True) as m:
            m.get(re.compile("/manage/health"), text='')
            m.post(
                re.compile("/api/v1/warehouse"),
                json={"orderItemUid": "item-1", "orderUid": "1-1-1", "model": "Lego 8880", "size": "L"}
            )
            m.post(re.compile("/api/v1/warranty/item-1"))

            response = test_client.post(
                "/api/v1/orders/1",
                json={"orderUid": "1-1-1", "model": "Lego 8880", "size": "L"}
            )
            assert response.status == "200 OK"
            assert response.json["orderUid"]

    with Session() as s:
        order = (
            s.query(Order)
            .filter(Order.order_uid == response.json["orderUid"])
            .one_or_none()
        )
        assert order
        assert order.order_uid == response.json["orderUid"]


def test_request_order(fresh_database, add_some_order):
    with app.test_client() as test_client:
        response = test_client.get("/api/v1/orders/1/1-1-1")
        assert response.json["itemUid"] == 'item-1'
        assert response.json["orderUid"] == '1-1-1'
        assert response.json["status"] == "PAID"


def test_request_all_orders(fresh_database, add_some_order):
    with app.test_client() as test_client:
        response = test_client.get("/api/v1/orders/1")
        assert response.json[0]["itemUid"] == 'item-1'
        assert response.json[0]["orderUid"] == '1-1-1'
        assert response.json[0]["status"] == "PAID"


def test_request_warranty(fresh_database, add_some_order):
    with app.test_client() as test_client:
        with requests_mock.Mocker(real_http=True) as m:
            m.get(re.compile("/manage/health"), text='')
            m.post(
                re.compile("/api/v1/warehouse"),
                json={"warrantyDate": "2020-11-11", "decision": "FIXING"}
            )
            response = test_client.post(
                "/api/v1/orders/1-1-1/warranty",
                json={"reason": "Broken"}
            )
            assert response.status == "200 OK"
            assert response.json["decision"] == "FIXING"


def test_request_delete_order(fresh_database, add_some_order):
    with app.test_client() as test_client:
        with requests_mock.Mocker(real_http=True) as m:
            m.get(re.compile("/manage/health"), text='')
            m.delete(re.compile("/api/v1/warehouse"))
            response = test_client.delete("/api/v1/orders/1-1-1")
            assert response.status == "204 NO CONTENT"
