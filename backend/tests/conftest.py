import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres@/filebrowser_test?host=/tmp&port=5433",
)

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


SAMPLE_CSV = """Name,Full Path,Size,Allocated,Files,Folders,% of Parent (Allocated),Last Modified,Last Accessed,Owner,Type,Dir Level (Relative)
Root,C:\\Root\\,1.5 GB,1.5 GB,5,2,100.0 %,01/05/2022,06/01/2024,alice,File Folder,0
Reports,C:\\Root\\Reports\\,1.0 GB,1.0 GB,3,0,66.6 %,02/10/2022,05/01/2024,alice,File Folder,1
deck.pptx,C:\\Root\\Reports\\deck.pptx,500 MB,500 MB,1,0,50.0 %,03/01/2022,01/01/2024,bob,PPTX File,2
notes.pptx,C:\\Root\\Reports\\notes.pptx,200 MB,200 MB,1,0,20.0 %,03/02/2022,01/15/2020,bob,PPTX File,2
data.xlsx,C:\\Root\\Reports\\data.xlsx,300 MB,300 MB,1,0,30.0 %,03/03/2022,06/01/2024,carol,XLSX File,2
Images,C:\\Root\\Images\\,500 MB,500 MB,2,0,33.3 %,02/11/2022,05/02/2024,alice,File Folder,1
a.jpg,C:\\Root\\Images\\a.jpg,300 MB,300 MB,1,0,60.0 %,03/04/2022,02/01/2024,dave,JPG File,2
b.png,C:\\Root\\Images\\b.png,200 MB,200 MB,1,0,40.0 %,03/05/2022,02/02/2024,dave,PNG File,2
"""


@pytest.fixture
def loaded(client):
    resp = client.post(
        "/api/datasets",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
        data={"name": "Sample"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
