#! /usr/bin/python

from __future__ import print_function
import sys
import os
import os.path
import tempfile
import logging
import yaml
import icat
import icat.config
from icat.query import Query
from helper import Unbuffered, DatafileBase, DatasetBase, MemorySpace

config = icat.config.Config(ids="mandatory")
config.add_variable('logfile', ("--logfile",), 
                    dict(help="logfile name template"),
                    optional=True)
config.add_variable('fileCount', ("--fileCount",), 
                    dict(help="number of data files in the dataset"),
                    type=int, default=4)
config.add_variable('fileSize', ("--fileSize",), 
                    dict(help="size of the data files"),
                    type=MemorySpace, default="20 MiB")
config.add_variable('source', ("--source",), 
                    dict(choices=["zero", "urandom", "file"], 
                         help="data source"),
                    default="zero")
config.add_variable('investigation', ("investigation",), 
                    dict(help="name of the investigation"))
conf = config.getconfig()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("upload-helper")
if conf.logfile:
    logfilename = conf.logfile % {'pid': os.getpid()}
    logfile = logging.FileHandler(logfilename, mode='wt')
    logformatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    logfile.setFormatter(logformatter)
    log.addHandler(logfile)
    log.propagate = False

client = icat.Client(conf.url, **conf.client_kwargs)
client.login(conf.auth, conf.credentials)


# Disable buffering of stdout.
sys.stdout = Unbuffered(sys.stdout)


class PreparedRandomDatafile(DatafileBase):

    rndfile = None

    @classmethod
    def prepareRandomFile(cls, size):
        cls.rndfile = tempfile.TemporaryFile()
        chunksize = 8192
        with open("/dev/urandom", "rb") as infile:
            while size > 0:
                if chunksize > size:
                    chunksize = size
                chunk = infile.read(chunksize)
                cls.rndfile.write(chunk)
                size -= len(chunk)
        cls.rndfile.flush()

    def __init__(self, size):
        super(PreparedRandomDatafile, self).__init__(size)
        assert self.rndfile, "no rndfile"
        self.data = os.fdopen(os.dup(self.rndfile.fileno()), 'rb')
        self.data.seek(0)

    def close(self):
        self.data.close()

    def _read(self, n):
        return self.data.read(n)


class Dataset(DatasetBase):
    fileCount = conf.fileCount
    fileSize = conf.fileSize


def getInvestigation():
    query = Query(client, "Investigation", conditions={
        "name": "= '%s'" % conf.investigation,
    })
    return client.assertedSearch(query)[0]

investigation = getInvestigation()
if conf.source == "file":
    PreparedRandomDatafile.prepareRandomFile(Dataset.fileSize)
    data = PreparedRandomDatafile
else:
    data = conf.source


print("READY")

while True:
    name = sys.stdin.readline().strip()
    if not name:
        break
    dataset = Dataset(client, investigation, name, data=data)
    try:
        statitem = dataset.uploadFiles(client)
        print("OK: %s" % yaml.dump(statitem.as_dict()).strip())
    except Exception as err:
        log.error("%s: %s", type(err).__name__, err)
        print("ERROR: %s" % err)

print("DONE")
