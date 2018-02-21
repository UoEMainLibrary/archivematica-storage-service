# -*- coding: utf8 -*-
"""Tests for the api.v3.urls module."""

from __future__ import print_function

import pytest

from locations.api.v3 import urls


def test_urls_construction():
    """Tests that we can configure URL routing for RESTful resources using a
    simple dict of resource member names.
    """
    # Configure routing and get a list of corresponding Django ``url``
    # instances as ``urlpatterns``
    resources_config = {
        'location': {},
        'package': {'pk_patt': urls.ID_PATT},  # Pretend pk for /packages/ is int id
        'space': {'searchable': False},  # Make /spaces/ non-searchable
        'pipeline': {},
    }
    route_builder = urls.RouteBuilder()
    route_builder.register_routes_for_resources(resources_config)
    urlpatterns = route_builder.get_urlpatterns()

    # Make assertions about ``urlpatterns``
    urlpatterns_names_regexes = sorted(
        [(up.name, up.regex.pattern) for up in urlpatterns])
    expected = [
        ('location', '^locations/(?P<pk>{})/$'.format(urls.UUID_PATT)),
        ('location_edit',
         '^locations/(?P<pk>{})/edit/$'.format(urls.UUID_PATT)),
        ('locations', '^locations/$'),
        ('locations_new', '^locations/new/$'),
        ('locations_new_search', '^locations/new_search/$'),
        ('locations_search', '^locations/search/$'),
        # Note the ID_PATT for /packages/ because of pk_patt above
        ('package', '^packages/(?P<pk>{})/$'.format(urls.ID_PATT)),
        ('package_edit',
         '^packages/(?P<pk>{})/edit/$'.format(urls.ID_PATT)),
        ('packages', '^packages/$'),
        ('packages_new', '^packages/new/$'),
        ('packages_new_search', '^packages/new_search/$'),
        ('packages_search', '^packages/search/$'),
        ('pipeline', '^pipelines/(?P<pk>{})/$'.format(urls.UUID_PATT)),
        ('pipeline_edit',
         '^pipelines/(?P<pk>{})/edit/$'.format(urls.UUID_PATT)),
        ('pipelines', '^pipelines/$'),
        ('pipelines_new', '^pipelines/new/$'),
        ('pipelines_new_search', '^pipelines/new_search/$'),
        ('pipelines_search', '^pipelines/search/$'),
        # Note that the /spaces/ resource has no search-related routes.
        ('space', '^spaces/(?P<pk>{})/$'.format(urls.UUID_PATT)),
        ('space_edit', '^spaces/(?P<pk>{})/edit/$'.format(urls.UUID_PATT)),
        ('spaces', '^spaces/$'),
        ('spaces_new', '^spaces/new/$')
    ]
    assert urlpatterns_names_regexes == expected

    # Make assertions about ``route_builder.routes``
    assert route_builder.routes[r'^locations/$'] == {
        'http_methods': {'GET': ('Locations', 'index'),
                         'POST': ('Locations', 'create'),
                         'SEARCH': ('Locations', 'search')},
        'route_name': 'locations'}
    assert route_builder.routes[
            r'^locations/(?P<pk>{})/$'.format(urls.UUID_PATT)] == {
        'http_methods': {'DELETE': ('Locations', 'delete'),
                         'GET': ('Locations', 'show'),
                         'PUT': ('Locations', 'update')},
        'route_name': 'location'}
    assert route_builder.routes[
            r'^locations/(?P<pk>{})/edit/$'.format(urls.UUID_PATT)] == {
        'http_methods': {'GET': ('Locations', 'edit')},
        'route_name': 'location_edit'}
    assert route_builder.routes['^locations/new/$'] == {
        'http_methods': {'GET': ('Locations', 'new')},
        'route_name': 'locations_new'}
    assert route_builder.routes['^locations/new_search/$'] == {
        'http_methods': {'GET': ('Locations', 'new_search')},
        'route_name': 'locations_new_search'}
    assert route_builder.routes['^locations/search/$'] == {
        'http_methods': {'POST': ('Locations', 'search')},
        'route_name': 'locations_search'}
    assert '^spaces/search/$' not in route_builder.routes
    assert '^pipelines/search/$' in route_builder.routes
    assert '^packages/search/$' in route_builder.routes
    assert r'^packages/(?P<pk>{})/$'.format(urls.ID_PATT) in route_builder.routes
    assert r'^packages/(?P<pk>{})/$'.format(
        urls.UUID_PATT) not in route_builder.routes


def test_regex_builders():
    """Test that the regex-building functions can build the correct regexes
    given resource names as input.
    """
    # Collection-targetting regex builder
    assert r'^frogs/$' == urls.get_collection_targetting_regex('frogs')
    assert r'^frogs/legs/$' == urls.get_collection_targetting_regex(
        'frogs', modifiers=['legs'])
    assert r'^frogs/legs/toes/$' == urls.get_collection_targetting_regex(
        'frogs', modifiers=['legs', 'toes'])
    assert r'^frogs/l/e/g/s/$' == urls.get_collection_targetting_regex(
        'frogs', modifiers='legs')
    with pytest.raises(TypeError):
        urls.get_collection_targetting_regex('frogs', modifiers=1)

    # Member-targetting regex builder
    assert r'^frogs/(?P<pk>{})/$'.format(
        urls.UUID_PATT) == urls.get_member_targetting_regex(
            'frogs', urls.UUID_PATT)
    assert r'^frogs/(?P<pk>{})/legs/$'.format(
        urls.ID_PATT) == urls.get_member_targetting_regex(
            'frogs', urls.ID_PATT, modifiers=['legs'])
    assert r'^frogs/(?P<pk>{})/legs/toes/$'.format(
        urls.UUID_PATT) == urls.get_member_targetting_regex(
            'frogs', urls.UUID_PATT, modifiers=['legs', 'toes'])
    assert r'^frogs/(?P<pk>{})/l/e/g/s/$'.format(
        urls.UUID_PATT) == urls.get_member_targetting_regex(
            'frogs', urls.UUID_PATT, modifiers='legs')
    with pytest.raises(TypeError):
        urls.get_member_targetting_regex('frogs', urls.UUID_PATT, modifiers=1)


def test_standard_routes():
    """Test that standard REST ``Route()``s are yielded from the aptly-named
    func.
    """
    cr, dr, er, ir, nr, sr, ur = urls.yield_standard_routes('sky')

    # POST /skies/
    assert cr.regex == '^skies/$'
    assert cr.name == 'skies'
    assert cr.http_method == 'POST'
    assert cr.class_name == 'Skies'
    assert cr.method_name == 'create'

    # DELETE /skies/<UUID>/
    assert dr.regex == r'^skies/(?P<pk>{})/$'.format(urls.UUID_PATT)
    assert dr.name == 'sky'
    assert dr.http_method == 'DELETE'
    assert dr.class_name == 'Skies'
    assert dr.method_name == 'delete'

    # GET /skies/<UUID>/edit/
    assert er.regex == r'^skies/(?P<pk>{})/edit/$'.format(urls.UUID_PATT)
    assert er.name == 'sky_edit'
    assert er.http_method == 'GET'
    assert er.class_name == 'Skies'
    assert er.method_name == 'edit'

    # GET /skies/
    assert ir.regex == '^skies/$'
    assert ir.name == 'skies'
    assert ir.http_method == 'GET'
    assert ir.class_name == 'Skies'
    assert ir.method_name == 'index'

    # GET /skies/new
    assert nr.regex == '^skies/new/$'
    assert nr.name == 'skies_new'
    assert nr.http_method == 'GET'
    assert nr.class_name == 'Skies'
    assert nr.method_name == 'new'

    # GET /skies/<UUID>/
    assert sr.regex == r'^skies/(?P<pk>{})/$'.format(urls.UUID_PATT)
    assert sr.name == 'sky'
    assert sr.http_method == 'GET'
    assert sr.class_name == 'Skies'
    assert sr.method_name == 'show'

    # PUT /skies/<UUID>/
    assert ur.regex == r'^skies/(?P<pk>{})/$'.format(urls.UUID_PATT)
    assert ur.name == 'sky'
    assert ur.http_method == 'PUT'
    assert ur.class_name == 'Skies'
    assert ur.method_name == 'update'


def test_search_routes():
    """Test that search-related ``Route()``s are yielded from the aptly-named
    func.
    """
    r1, r2, r3 = urls.yield_search_routes('octopus')

    # SEARCH /octopodes/
    assert r1.regex == '^octopodes/$'
    assert r1.name == 'octopodes'
    assert r1.http_method == 'SEARCH'
    assert r1.class_name == 'Octopodes'
    assert r1.method_name == 'search'

    # POST /octopodes/search/
    assert r2.regex == '^octopodes/search/$'
    assert r2.name == 'octopodes_search'
    assert r2.http_method == 'POST'
    assert r2.class_name == 'Octopodes'
    assert r2.method_name == 'search'

    # GET /octopodes/new_search/
    assert r3.regex == '^octopodes/new_search/$'
    assert r3.name == 'octopodes_new_search'
    assert r3.http_method == 'GET'
    assert r3.class_name == 'Octopodes'
    assert r3.method_name == 'new_search'
