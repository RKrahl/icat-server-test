"""Test connect to an ICAT and an IDS server and query the version.
"""

from __future__ import print_function
import pytest
import icat
import icat.config
from conftest import getConfig


def test_get_icat_version():
    """Query the version from the test ICAT server.

    This implicitly tests that the test ICAT server is properly
    configured and that we can connect to it.
    """

    conf = getConfig(needlogin=False)
    client = icat.Client(conf.url, **conf.client_kwargs)
    # python-icat supports ICAT server 4.2 or newer.  But actually, we
    # just want to check that client.apiversion is set and supports
    # comparison with version strings.
    assert client.apiversion >= '4.2'
    print("\nConnect to %s\nICAT version %s\n" % (conf.url, client.apiversion))


def test_get_ids_version():
    """Query the version from the test IDS server.

    This implicitly tests that the test ICAT and IDS servers are
    properly configured and that we can connect to them.
    """

    conf = getConfig(needlogin=False, ids="mandatory")
    client = icat.Client(conf.url, **conf.client_kwargs)
    # python-icat supports all publicly released IDS server version,
    # e.g. 1.0.0 or newer.  But actually, we just want to check that
    # client.apiversion is set and supports comparison with version
    # strings.
    assert client.ids.apiversion >= '1.0.0'
    print("\nConnect to %s\nIDS version %s\n" 
          % (conf.idsurl, client.ids.apiversion))
