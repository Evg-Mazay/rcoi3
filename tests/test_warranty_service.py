from datetime import date
import json

from database import Session
from warranty_service import app, Warranty, Status


TEST_WARRANTY = {
    "item_uid": "1-1-1",
    "status": Status.on,
    "warranty_date": date.today(),
}


def test_request_start_warranty(fresh_database):
    with app.test_client() as test_client:
        response = test_client.post("/api/v1/warranty/1-1-1")
        assert response.status_code == 204
    with Session() as s:
        created_warranty = s.query(Warranty).filter(Warranty.item_uid == "1-1-1").one_or_none()
        assert created_warranty.status == Status.on


def test_request_warranty_status(fresh_database):
    with Session() as s:
        s.add(Warranty(**TEST_WARRANTY))
    with app.test_client() as test_client:
        response = test_client.get("/api/v1/warranty/1-1-1")
        assert response.status_code == 200
        assert json.loads(response.data)["status"] == TEST_WARRANTY["status"]

        bad_response = test_client.get("/api/v1/warranty/2-2-2")
        assert bad_response.status_code == 404
        assert "message" in json.loads(bad_response.data)


def test_request_stop_warranty(fresh_database):
    with Session() as s:
        s.add(Warranty(**TEST_WARRANTY))
    with app.test_client() as test_client:
        response = test_client.delete("/api/v1/warranty/1-1-1")
        assert response.status_code == 204
    with Session() as s:
        created_warranty = s.query(Warranty).filter(Warranty.item_uid == "1-1-1").one_or_none()
        assert created_warranty.status == Status.removed


def test_request_warranty_result(fresh_database):
    with Session() as s:
        s.add(Warranty(**TEST_WARRANTY))
    with app.test_client() as test_client:
        response = test_client.post("/api/v1/warranty/1-1-1/warranty",
                                    json={"reason": "", "availableCount": 1})
        assert response.status_code == 200
        assert json.loads(response.data)["decision"] == "RETURN"

        response = test_client.post("/api/v1/warranty/1-1-1/warranty",
                                    json={"reason": "", "availableCount": 0})
        assert response.status_code == 200
        assert json.loads(response.data)["decision"] == "FIXING"
