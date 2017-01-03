"""Test upload a large amount of dataand download of files to and from IDS.
"""

from __future__ import print_function
import time
import logging
from timeit import default_timer as timer
import random
import tempfile
import zipfile
import pytest
import icat
import icat.config
from icat.query import Query
from icat.exception import SearchAssertionError, IDSDataNotOnlineError
from conftest import getConfig, wipe_data, DummyDatafile
from conftest import Time, MemorySpace


log = logging.getLogger("test.%s" % __name__)


# ============================ testdata ============================

testInvestigation = "12100409-ST"
testDatasets = []

def getDatafileFormat(client):
    query = Query(client, "DatafileFormat", conditions={
        "name": "= 'raw'",
    })
    return (client.assertedSearch(query)[0])


class Dataset(object):

    size = None

    def __init__(self, client, name, fileCount, fileSize):
        self.name = name
        self.fileCount = fileCount
        self.fileSize = fileSize
        size = fileCount*fileSize
        if size != self.size:
            self.size = size

        query = Query(client, "Investigation", conditions={
            "name": "= '%s'" % testInvestigation,
        })
        investigation = client.assertedSearch(query)[0]
        query = Query(client, "DatasetType", conditions={
            "name": "= 'raw'",
        })
        datasetType = client.assertedSearch(query)[0]
        dataset = client.new("dataset", name=self.name, complete=False, 
                             investigation=investigation, type=datasetType)
        dataset.create()
        self.dataset = dataset

    def uploadFiles(self, client, dfformat):
        start = timer()
        for n in range(1,self.fileCount+1):
            name = "test_%05d.dat" % n
            f = DummyDatafile(self.fileSize)
            datafile = client.new("datafile", name=name,
                                  dataset=self.dataset, datafileFormat=dfformat)
            df = client.putData(f, datafile)
            crc = f.getcrc()
            assert df.location is not None
            assert df.fileSize == f.size
            assert df.checksum == crc
        end = timer()
        elapsed = Time(end - start)
        log.info("Uploaded %s to dataset %s in %s (%s/s)", 
                 self.size, self.name, elapsed, MemorySpace(self.size/elapsed))

    def download(self, client):
        query = Query(client, "Datafile", conditions={
            "dataset.id": "= %d" % self.dataset.id,
        })
        datafiles = client.search(query)
        assert len(datafiles) == self.fileCount
        with tempfile.TemporaryFile() as f:
            start = timer()
            while True:
                try:
                    response = client.getData([self.dataset])
                    break
                except IDSDataNotOnlineError:
                    log.info("Wait for dataset %s to become online.", 
                             self.name)
                    time.sleep(1)
            size = MemorySpace(copyfile(response, f))
            end = timer()
            zf = zipfile.ZipFile(f, 'r')
            zinfos = zf.infolist()
            assert len(zinfos) == len(datafiles)
            for df in datafiles:
                zi = None
                for i in zinfos:
                    if i.filename.endswith(df.name):
                        zi = i
                        break
                assert zi is not None
                assert "%x" % (zi.CRC & 0xffffffff) == df.checksum
                assert zi.file_size == df.fileSize
        elapsed = Time(end - start)
        log.info("Downloaded %s for dataset %s in %s (%s/s)", 
                 self.size, self.name, elapsed, MemorySpace(self.size/elapsed))

class SmallDataset(Dataset):
    size = 4*MemorySpace("512 KiB")
    def __init__(self, client, name):
        s = MemorySpace("512 KiB")
        super(SmallDataset, self).__init__(client, name, 4, s)

class ManyFileDataset(Dataset):
    size = 1000*MemorySpace("51.2 KiB")
    def __init__(self, client, name):
        s = MemorySpace("51.2 KiB")
        super(ManyFileDataset, self).__init__(client, name, 1000, s)

class BigDataset(Dataset):
    size = 2*MemorySpace("1 GiB")
    def __init__(self, client, name):
        s = MemorySpace("1 GiB")
        super(BigDataset, self).__init__(client, name, 2, s)

def createDatasets(client, testConfig):
    count = int(0.8*testConfig.baseSize/SmallDataset.size)
    for i in range(1, count+1):
        name = "%s-a%05d" % (testConfig.moduleName, i)
        testDatasets.append(SmallDataset(client, name))
    count = int(0.2*testConfig.baseSize/ManyFileDataset.size)
    for i in range(1, count+1):
        name = "%s-b%05d" % (testConfig.moduleName, i)
        testDatasets.append(ManyFileDataset(client, name))
    count = int(9.0*testConfig.baseSize/BigDataset.size)
    for i in range(1, count+1):
        name = "%s-c%05d" % (testConfig.moduleName, i)
        testDatasets.append(BigDataset(client, name))

# ============================= helper =============================

@pytest.fixture(scope="module")
def client(setupicat, testConfig, request):
    conf = getConfig(ids="mandatory")
    client = icat.Client(conf.url, **conf.client_kwargs)
    client.login(conf.auth, conf.credentials)
    def cleanup():
        query = Query(client, "Dataset", conditions={
            "name": "LIKE '%s-%%'" % testConfig.moduleName
        })
        wipe_data(client, query)
        query.setLimit( (0,500) )
        while True:
            objs = client.search(query)
            if not objs:
                break
            client.deleteMany(objs)
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    return client

def copyfile(infile, outfile, chunksize=8192):
    """Read all data from infile and write them to outfile.
    """
    size = 0
    while True:
        chunk = infile.read(chunksize)
        if not chunk:
            break
        outfile.write(chunk)
        size += len(chunk)
    return size

# ============================= tests ==============================

def test_upload(client, testConfig):
    createDatasets(client, testConfig)
    random.shuffle(testDatasets)
    datafileformat = getDatafileFormat(client)
    totalsize = 0
    start = timer()
    for dataset in testDatasets:
        dataset.uploadFiles(client, datafileformat)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Uploaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))

def test_download(client):
    # Dowload each dataset five time in average
    count = 5*len(testDatasets)
    totalsize = 0
    start = timer()
    for i in range(count):
        dataset = random.choice(testDatasets)
        dataset.download(client)
        totalsize += dataset.size
    end = timer()
    elapsed = Time(end - start)
    log.info("Downloaded %s in %s (%s/s)", 
             MemorySpace(totalsize), elapsed, MemorySpace(totalsize/elapsed))
