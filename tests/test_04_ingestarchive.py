"""Ingest by directly writing a ZIP file to archive storage.
"""

from __future__ import print_function
import os
import os.path
import zlib
import logging
import pytest
import icat
import icat.config
from icat.query import Query
from icat.hzb.proposal import ProposalNo
from helper import MemorySpace, DatasetBase
from conftest import getConfig, callscript


log = logging.getLogger("test.%s" % __name__)

# ============================ testdata ============================

testProposalNo = "12100409-ST-1.1-P"
testDatasetPrefix = "test_ingestarchive"

# ============================= helper =============================


@pytest.fixture(scope="module")
def icatconfig(setupicat, testConfig, request):
    conf = getConfig(ids="mandatory")
    mainbase = request.config.getini('mainstoragebase')
    archivebase = request.config.getini('archivestoragebase')
    conf.cmdargs.append("--mainStorageBase=%s" % mainbase)
    conf.cmdargs.append("--archiveStorageBase=%s" % archivebase)
    incomingbase = request.config.getini('incomingbase')
    proposaldir = os.path.join(incomingbase, testProposalNo.replace('/', '_'))
    os.mkdir(proposaldir)
    conf.proposaldir = proposaldir
    def cleanup():
        os.rmdir(proposaldir)
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    return conf

@pytest.fixture(scope="module")
def client(icatconfig):
    client = icat.Client(icatconfig.url, **icatconfig.client_kwargs)
    client.login(icatconfig.auth, icatconfig.credentials)
    return client

@pytest.fixture(scope="function")
def dataset(request, icatconfig, client, testConfig):
    assert request.node.name.startswith("test_")
    name = testDatasetPrefix + request.node.name[4:]
    dataset = Dataset(icatconfig.proposaldir, testProposalNo, name)
    def cleanup():
        dataset.cleanup(client)
    if testConfig.cleanup:
        request.addfinalizer(cleanup)
    return dataset


class Datafile(object):

    def __init__(self, directory, fname, size):
        self.directory = directory
        self.fname = fname
        self.size = size
        self.path = os.path.join(self.directory, self.fname)
        self.crc32 = 0
        self._create()

    def _create(self):
        size = self.size
        chunksize = 8192
        with open("/dev/urandom", "rb") as infile:
            with open(self.path, "wb") as outfile:
                while size > 0:
                    if chunksize > size:
                        chunksize = size
                    chunk = infile.read(chunksize)
                    self.crc32 = zlib.crc32(chunk, self.crc32)
                    outfile.write(chunk)
                    size -= len(chunk)

    def getcrc(self):
        return "%x" % (self.crc32 & 0xffffffff)

    def unlink(self):
        os.unlink(self.path)


class Dataset(DatasetBase):

    fileCount = 32
    fileSize = MemorySpace("10 MiB")

    def __init__(self, incomingdir, proposal, name):
        self.proposal = ProposalNo(proposal)
        self.name = name
        self.files = []
        self.datasetdir = os.path.join(incomingdir, name)
        os.mkdir(self.datasetdir)
        for n in range(1,self.fileCount+1):
            fname = "test_%05d.dat" % n
            datafile = Datafile(self.datasetdir, fname, self.fileSize)
            self.files.append(datafile)
        self.size = self.fileCount*self.fileSize
        self.dataset = None

    def cleanup(self, client):
        for f in self.files:
            f.unlink()
        os.rmdir(self.datasetdir)
        client.deleteData([self.dataset])
        client.delete(self.dataset)

    def uploadFiles(self, client):
        raise RuntimeError("This Dataset class does not support upload.")

    def ingest(self, conf):
        args = conf.cmdargs + [str(self.proposal), self.name]
        args.extend(f.path for f in self.files)
        log.info("%s: call ingest script", self.name)
        callscript("addfile-archive.py", args)
        log.info("%s: ingest complete", self.name)

    def verify(self, client):
        query = icat.query.Query(client, "Dataset",
                                 conditions={"name":"= '%s'" % self.name}, 
                                 includes=["datafiles"])
        query.addConditions({"investigation.name":"='%s'" % self.proposal.name})
        if self.proposal.visitId:
            query.addConditions({"investigation.visitId":
                                 "='%s'" % self.proposal.visitId})
        self.dataset = client.assertedSearch(query)[0]
        assert len(self.dataset.datafiles) == len(self.files), \
            "wrong number of datafiles"
        dfidx = dict([ (df.name,df) for df in self.dataset.datafiles ])
        for f in self.files:
            assert f.fname in dfidx
            df = dfidx[f.fname]
            assert df.location
            assert df.fileSize == f.size
            assert df.checksum == f.getcrc()

# ============================= tests ==============================

def test_ingest(icatconfig, client, dataset):
    dataset.ingest(icatconfig)
    dataset.verify(client)
    dataset.download(client)
