"""Test upload and download of files to and from IDS.
"""

from __future__ import print_function
import tempfile
import zipfile
import pytest
import icat
import icat.config
from icat.query import Query
from icat.ids import DataSelection
from icat.exception import SearchAssertionError
from conftest import getConfig, wipe_datafiles, DummyDatafile


# ============================ testdata ============================

sizeB = 1
sizeKiB = 1024*sizeB
sizeMiB = 1024*sizeKiB
sizeGiB = 1024*sizeMiB
sizeTiB = 1024*sizeGiB

testInvestigation = "12100409-ST"
testDSName = "testUpload"
testFSize = 1*sizeGiB
testFCount = 5
testDatafiles = []

# ============================= helper =============================

def createDataset(client):
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % testInvestigation,
    })
    investigation = client.assertedSearch(query)[0]
    query = Query(client, "DatasetType", conditions={
        "name": "= 'raw'",
    })
    datasetType = client.assertedSearch(query)[0]
    dataset = client.new("dataset", name=testDSName, complete=False, 
                         investigation=investigation, type=datasetType)
    dataset.create()
    return dataset

def getDataset(client):
    query = Query(client, "Dataset", conditions={
        "investigation.name": "= '%s'" % testInvestigation,
        "name": "= '%s'" % testDSName,
    })
    return (client.assertedSearch(query)[0])

def getDatafileFormat(client):
    query = Query(client, "DatafileFormat", conditions={
        "name": "= 'raw'",
    })
    return (client.assertedSearch(query)[0])

@pytest.fixture(scope="module")
def client(setupicat, request):
    conf = getConfig(ids="mandatory")
    client = icat.Client(conf.url, **conf.client_kwargs)
    client.login(conf.auth, conf.credentials)
    def cleanup():
        try:
            dataset = getDataset(client)
        except SearchAssertionError:
            pass
        else:
            query = Query(client, "Datafile", 
                          conditions={"dataset.id": "= %d" % dataset.id})
            wipe_datafiles(client, query)
            client.delete(dataset)
    request.addfinalizer(cleanup)
    return client

def copyfile(infile, outfile, chunksize=8192):
    """Read all data from infile and write them to outfile.
    """
    while True:
        chunk = infile.read(chunksize)
        if not chunk:
            break
        outfile.write(chunk)

# ============================= tests ==============================

def test_upload(client):
    dataset = createDataset(client)
    datafileformat = getDatafileFormat(client)
    for n in range(1,testFCount+1):
        name = "test_%05d.dat" % n
        f = DummyDatafile(testFSize)
        datafile = client.new("datafile", name=name,
                              dataset=dataset, datafileFormat=datafileformat)
        print("\nUpload file %s" % name)
        df = client.putData(f, datafile)
        crc = f.getcrc()
        assert df.location is not None
        assert df.fileSize == f.size
        assert df.checksum == crc
        testDatafiles.append({"name": name, "size": f.size, "crc": crc})

def test_download(client):
    dataset = getDataset(client)
    print("\nDownload dataset %s" % testDSName)
    response = client.getData([dataset])
    with tempfile.TemporaryFile() as f:
        copyfile(response, f)
        zf = zipfile.ZipFile(f, 'r')
        zinfos = zf.infolist()
        assert len(zinfos) == len(testDatafiles)
        for df in testDatafiles:
            zi = None
            for i in zinfos:
                if i.filename.endswith(df['name']):
                    zi = i
                    break
            assert zi is not None
            assert "%x" % (zi.CRC & 0xffffffff) == df['crc']
            assert zi.file_size == df['size']