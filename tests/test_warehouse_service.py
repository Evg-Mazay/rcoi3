from datetime import date
import json
import re

import requests_mock

from database import Session
from warehouse_service import app, refresh_items_in_db, Item, OrderItem


TEST_ORDER = {
    "orderUid": "1-1-1",
    "model": "Lego 8880",
    "size": "L",
}


def test_request_new_item(fresh_database):
    refresh_items_in_db()
    with app.test_client() as test_client:
        response = test_client.post("/api/v1/warehouse", json=TEST_ORDER)
        assert response.status_code == 200
        response = json.loads(response.data)
        assert response["orderUid"] == TEST_ORDER["orderUid"]
        assert response["model"] == TEST_ORDER["model"]
        assert response["size"] == TEST_ORDER["size"]
        with Session() as s:
            assert s.query(Item).get(3).available_count == 9999


def test_request_get_info(fresh_database):
    refresh_items_in_db()
    with app.test_client() as test_client:
        order_item_id = json.loads(test_client.post("/api/v1/warehouse",
                                                    json=TEST_ORDER).data)["orderItemUid"]
        response = test_client.get("/api/v1/warehouse/" + order_item_id)
        assert response.status_code == 200
        response = json.loads(response.data)
        assert response["size"] == TEST_ORDER["size"]
        assert response["model"] == TEST_ORDER["model"]


def test_request_warranty(fresh_database):
    refresh_items_in_db()
    with Session() as s:
        s.add(OrderItem(item_id=1, order_item_uid="item-1", order_uid='1-1-1'))
    with app.test_client() as test_client:
        with requests_mock.Mocker(real_http=True) as m:
            m.get(re.compile("/manage/health"), text='')
            m.post(
                re.compile("/api/v1/warranty/item-1/warranty"),
                json={"warrantyDate": "2020-11-11", "decision": "FIXING"}
            )
            response = test_client.post("/api/v1/warehouse/item-1/warranty", json={"reason": "Broken"})
            assert response.json["decision"] == "FIXING"


def test_request_remove_item(fresh_database):
    refresh_items_in_db()
    with Session() as s:
        s.add(OrderItem(item_id=1, order_item_uid="item-1", order_uid='1-1-1'))
    with app.test_client() as test_client:
        response = test_client.delete("/api/v1/warehouse/item-1")
        assert response.status_code == 204
        with Session() as s:
            assert s.query(Item).get(1).available_count == 10001

