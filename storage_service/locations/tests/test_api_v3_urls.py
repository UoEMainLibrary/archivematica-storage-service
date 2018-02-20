# -*- coding: utf8 -*-
"""Tests for the api.v3.urls module."""

from __future__ import print_function
from functools import partial
from mock import patch, MagicMock
from uuid import uuid4

from django.core.urlresolvers import ResolverMatch
from django.http import HttpRequest

from locations.api.v3 import urls, resources
from locations.api.v3.resources import Packages


FAKE_RESP = {'msg': 'hello from the resource'}


class FakeResource(object):

    def __init__(self, *args,**kwargs):
        self.sigma = {}

    def __getattr__(self, name):
        print('name FUCK')
        print(name)
        return partial(self.all_purpose, self, name)

    def all_purpose(self, name, *args, **kwargs):
        sigma = self.sigma.get(name, [])
        sigma.append({'args': args, 'kwargs': kwargs})
        self.sigma[name] = sigma
        return FAKE_RESP

"""

    def show(self, pk):
        return FAKE_RESP

    def new(self):
        return FAKE_RESP

    def new_search(self):
        return FAKE_RESP

    def edit(self, pk):
        return FAKE_RESP

    def delete(self, pk):
        return FAKE_RESP

    def update(self, pk):
        return FAKE_RESP

    def create(self):
        return FAKE_RESP

    def index(self):
        return FAKE_RESP

    def search(self):
        return FAKE_RESP
"""


#@patch('locations.api.v3.resources.Packages', return_value=FakeResource())
#@patch('locations.api.v3.resources.Pipelines', return_value=FakeResource())
@patch.object(Packages, 'show')
def test_urls_construction(packages_show):
    """Tests that we can configure URL routing for RESTful resources using a
    simple dict of resource member names.
    """
    print(packages_show)
    print(type(packages_show))
    resources_config = {
        'package': {},
        'pipeline': {'searchable': False},
    }
    dispatcher = urls.Dispatcher()
    urls.register_resource_url_patterns(dispatcher, resources_config)
    urlpatterns = dispatcher.get_urls()
    urlpatterns_names_regexes = sorted(
        [(up.name, up.regex.pattern) for up in urlpatterns])
    assert urlpatterns_names_regexes == [
        ('package', r'^packages/(?P<pk>\w{8}-\w{4}-\w{4}-\w{4}-\w{12})/$'),
        ('package_edit',
         r'^packages/(?P<pk>\w{8}-\w{4}-\w{4}-\w{4}-\w{12})/edit/$'),
        ('package_new', r'^packages/new/$'),
        ('packages', r'^packages/$'),
        ('packages_new_search', r'^packages/new_search/$'),
        ('packages_search_post', r'^packages/search/$'),
        ('pipeline', r'^pipelines/(?P<pk>\w{8}-\w{4}-\w{4}-\w{4}-\w{12})/$'),
        ('pipeline_edit',
         r'^pipelines/(?P<pk>\w{8}-\w{4}-\w{4}-\w{4}-\w{12})/edit/$'),
        ('pipeline_new', r'^pipelines/new/$'),
        ('pipelines', r'^pipelines/$')
    ]

    urlpatternsdict = {up.name: up for up in urlpatterns}
    uuid = str(uuid4())
    pipeline_url = urlpatternsdict['pipeline']
    resolver_match = pipeline_url.resolve('pipelines/{}/'.format(uuid))
    assert isinstance(resolver_match, ResolverMatch)
    request = HttpRequest()
    request.method = 'GET'
    print('FUCK')
    print(resolver_match.func)
    resolver_match.func(request, *resolver_match.args, **resolver_match.kwargs)
    print(packages_show.call_count)
