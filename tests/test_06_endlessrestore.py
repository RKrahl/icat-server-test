"""Archive and restore a dataset.

The idea behind the test is to artificially block the restore in the
file system and then to observe whether the DsRestorer times out.
That is why we wait after archive for a manual intervention.
"""

from __future__ import print_function
import os.path
import time
import tempfile
import logging
import pytest
import icat
import icat.config
from icat.ids import DataSelection
from icat.query import Query
icat.hzb = pytest.importorskip("icat.hzb") # This test module is HZB specific
from helper import MemorySpace, DatasetBase
from conftest import getConfig, wipe_data


log = logging.getLogger("test.%s" % __name__)


# ============================ testdata ============================

testInvestigation = "gate1:12100409-ST-1.1-P"
testDatasetName = "test_endlessrestore"
testDatasets = []

class Dataset(DatasetBase):
    fileCount = 4
    fileSize = MemorySpace("20 MiB")

def createDatasets(client):
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    inv = client.assertedSearch(query)[0]
    testDatasets.append(Dataset(client, inv, testDatasetName))
    return testDatasets

# ============================= helper =============================

@pytest.fixture(scope="module")
def icatconfig(setupicat, testConfig, request):
    client, conf, config = getConfig(ids="mandatory")
    def cleanup():
        client.login(conf.auth, conf.credentials)
        query = Query(client, "Dataset", conditions={
            "name": "LIKE '%s%%'" % testDatasetName
        })
        wipe_data(client, query)
        client.logout()
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    return (conf, config)

def getStatus(client, ds):
    status = client.ids.getStatus(DataSelection([ds.dataset]))
    log.info("%s: %s", ds.name, status)
    return status

def wait():
    prefix = "%s-" % testDatasetName
    with tempfile.NamedTemporaryFile(prefix=prefix, delete=False) as f:
        log.info("Waiting for %s to vanish ...", f.name)
        while os.path.isfile(f.name):
            time.sleep(60)

# ============================= tests ==============================

def test_upload(icatconfig):
    conf, config = icatconfig
    client = icat.Client(conf.url, **config.client_kwargs)
    client.login(conf.auth, conf.credentials)
    createDatasets(client)
    assert len(testDatasets) > 0
    for dataset in testDatasets:
        dataset.uploadFiles(client)
    client.logout()

def test_archive_and_wait(icatconfig):
    conf, config = icatconfig
    client = icat.Client(conf.url, **config.client_kwargs)
    client.login(conf.auth, conf.credentials)
    for dataset in testDatasets:
        client.ids.archive(DataSelection([dataset.dataset]))
    time.sleep(10)
    for dataset in testDatasets:
        getStatus(client, dataset)
    client.logout()
    wait()

def test_restore(icatconfig):
    conf, config = icatconfig
    hour = 60*60
    client = icat.Client(conf.url, **config.client_kwargs)
    client.login(conf.auth, conf.credentials)
    for dataset in testDatasets:
        client.ids.restore(DataSelection([dataset.dataset]))
    while True:
        alldone = True
        for dataset in testDatasets:
            if getStatus(client, dataset) != "ONLINE":
                alldone = False
        if alldone:
            break
        else:
            time.sleep(hour)
            client.refresh()
    client.logout()

def test_download(icatconfig):
    conf, config = icatconfig
    client = icat.Client(conf.url, **config.client_kwargs)
    client.login(conf.auth, conf.credentials)
    for dataset in testDatasets:
        dataset.download(client)
    client.logout()
