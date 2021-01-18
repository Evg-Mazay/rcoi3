from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import create_schema, Session


@pytest.fixture()
def fresh_database():
    engine = create_engine("sqlite:///:memory:")
    with patch.object(Session, "__init__", return_value=None), \
            patch.object(Session, "session_class", side_effect=sessionmaker(bind=engine)),\
            patch("database.engine", return_value=engine):
        create_schema(engine_=engine)
        yield
