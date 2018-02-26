"""Version 3 of the Storage Service API.

The Storage Service exposes the following resources via a consistent HTTP JSON
interface under the path namespace /api/v3/:

- /locations/ --- purpose-specific paths within a /spaces/ resource
- /packages/ --- Information Package (SIP, DIP or AIP)
- /spaces/ --- storage space with behaviour specific to backing system
- /pipelines/ --- an Archivematica instance that is the source of a package

The following resources may be exposed later:

- /file/ --- a file on disk (which is in a package), represented as db row.
- /fsobjects/ --- directories and files on disk, read-only, no database models

All resources have endpoints that follow this pattern::

    +-----------------+-------------+----------------------------+--------+
    | Purpose         | HTTP Method | Path                       | Method |
    +-----------------+-------------+----------------------------+--------+
    | Create new      | POST        | /<cllctn_name>/            | create |
    | Create data     | GET         | /<cllctn_name>/new/        | new    |
    | Read all        | GET         | /<cllctn_name>/            | index  |
    | Read specific   | GET         | /<cllctn_name>/<id>/       | show   |
    | Update specific | PUT         | /<cllctn_name>/<id>/       | update |
    | Update data     | GET         | /<cllctn_name>/<id>/edit/  | edit   |
    | Delete specific | DELETE      | /<cllctn_name>/<id>/       | delete |
    | Search          | SEARCH      | /<cllctn_name>/            | search |
    | Search          | POST        | /<cllctn_name>/search/     | search |
    | Search data     | GET         | /<cllctn_name>/new_search/ | search |
    +-----------------+-------------+----------------------------+--------+

.. note:: To remove the search-related routes for a given resource, create a
   ``'searchable'`` key with value ``False`` in the configuration for the
   resource in the ``RESOURCES`` dict. E.g., ``'location': {'searchable':
   False}`` will make the /locations/ resource non-searchable.

.. note:: All resources expose the same endpoints. If a resource needs special
   treatment, it should be done at the corresponding class level. E.g., if
   ``POST /packages/`` (creating a package) is special, then do special stuff
   in ``resources.py::Packages.create``. Similarly, if packages are indelible,
   then ``resources.py::Packages.delete`` should return 404.

"""

from __future__ import absolute_import
from collections import namedtuple
from functools import partial
from itertools import chain
import logging
import string

from django.conf.urls import url
from django.http import JsonResponse
import inflect

from . import resources

LOGGER = logging.getLogger(__name__)
UUID_PATT = r'\w{8}-\w{4}-\w{4}-\w{4}-\w{12}'
ID_PATT = r'\d+'
ROUTE_NAME_CHARS = string.letters + '_'
INFLP = inflect.engine()
INFLP.classical()

# ==============================================================================
#   Resource Configuration
# ==============================================================================

# This dict is configuration for the resources that the SS exposes. All of the
# keys correspond to resources that will receive the standard methods/actions
# (create, new, index, show, update, edit, and delete). The config dict value
# controls additional configuration for the resource. For example, adding
# ``'searchable': False`` will make the resource non-searchable.
RESOURCES = {
    #'fsobject': {},
    'location': {},
    'package': {},
    'space': {},
    'pipeline': {},
    # 'file': {}
}

RESOURCE_ACTIONS = ('create',
                    'delete',
                    'edit',
                    'index',
                    'new',
                    'show',
                    'update')
COLLECTION_TARGETTING = ('create', 'index')
MEMBER_TARGETTING = ('delete', 'show', 'update')
DEFAULT_METHOD = 'GET'
ACTIONS2METHODS = {'create': 'POST',
                   'delete': 'DELETE',
                   'update': 'PUT'}
OK_STATUS = 200
METHOD_NOT_ALLOWED_STATUS= 405


class RouteBuilder(object):
    """A builder of routes: register a bunch of ``Route`` instances with it and
    later ask it for a list of corresponding Django ``url`` instances. A
    "route" is a path regex, a route name, an HTTP method, and a class/method
    to call when the path and HTTP method match a request.
    """

    def __init__(self):
        self.routes = {}

    def register_route(self, route):
        """Register a ``Route()`` instance by breaking it apart and storing it
        in ``self.routes``, keyed by its regex and then by its HTTP method.
        """
        config = self.routes.get(route.regex, {})
        config['route_name'] = route.name
        http_methods_config = config.get('http_methods', {})
        http_methods_config[route.http_method] = (
            route.class_name, route.method_name)
        config['http_methods'] = http_methods_config
        self.routes[route.regex] = config

    def get_urlpatterns(self):
        """Return ``urlpatterns_``, a list of Django ``url`` instances that
        cause the appropriate instance method to be called for a given request.
        Because Django does not allow the HTTP method to determine what is
        called, we must supply a view to ``url`` as an anonymous function with
        a closure over the route "config", which the anonymous function can use
        to route the request to the appropriate method call. For example,
        ``GET /pipelines/`` and ``POST /pipelines/`` are handled by the same
        function, but ultimately the former is routed to ``Pipelines().index``
        and the latter to ``Pipelines().create``.
        """
        urlpatterns_ = []
        for regex, config in self.routes.items():
            route_name = config['route_name']
            def resource_callable(config, request, **kwargs):
                http_methods_config = config['http_methods']
                try:
                    class_name, method_name = http_methods_config[
                        request.method]
                except KeyError:
                    return JsonResponse(
                        method_not_allowed(
                            request.method, list(http_methods_config.keys())),
                        status=METHOD_NOT_ALLOWED_STATUS)
                resource_cls = getattr(resources, class_name)
                instance = resource_cls(request)
                method = getattr(instance, method_name)
                response = method(**kwargs)
                try:
                    response, status = response
                except (ValueError, TypeError):
                    status = OK_STATUS
                return JsonResponse(response, status=status)
            urlpatterns_.append(url(
                regex,
                # Sidestep Python's late binding:
                view=partial(resource_callable, config),
                name=route_name))
        return urlpatterns_

    def register_routes_for_resource(self, rsrc_member_name, rsrc_config):
        """Register all of the routes generable for the resource with member
        name ``rsrc_member_name`` and with configuration ``rsrc_config``. The
        ``rsrc_config`` can control whether the resource is searchable and what
        its primary key regex (``pk_patt``) should be.
        """
        routes = []
        if rsrc_config.get('searchable', True):
            routes.append(yield_search_routes(rsrc_member_name))
        routes.append(yield_standard_routes(
            rsrc_member_name, pk_patt=rsrc_config.get('pk_patt', UUID_PATT)))
        for route in chain(*routes):
            self.register_route(route)

    def register_routes_for_resources(self, resources_):
        """Register all of the routes generable for each resource configured in
        the ``resources_`` dict.
        """
        for rsrc_member_name, rsrc_config in resources_.items():
            self.register_routes_for_resource(rsrc_member_name, rsrc_config)


# A "route" is a unique combination of path regex, route name, HTTP method, and
# class/method to call when the path regex and HTTP method match a request.
# Note that because of how Django's ``url`` works, multiple distinct routes can
# have the same ``url`` instance with the same name; e.g., POST /pipelines/ and
# GET /pipelines/ are both handled by the "pipelines" ``url``.
Route = namedtuple('Route', 'name regex http_method class_name method_name')


def get_collection_targetting_regex(rsrc_collection_name, modifiers=None):
    """Return a regex of the form '^<rsrc_collection_name>/$'
    with optional trailing modifiers, e.g., '^<rsrc_collection_name>/new/$'.
    """
    if modifiers:
        return r'^{rsrc_collection_name}/{modifiers}/$'.format(
            rsrc_collection_name=rsrc_collection_name,
            modifiers='/'.join(modifiers))
    return r'^{rsrc_collection_name}/$'.format(
        rsrc_collection_name=rsrc_collection_name)


def get_member_targetting_regex(rsrc_collection_name, pk_patt, modifiers=None):
    """Return a regex of the form '^<rsrc_collection_name>/<pk>/$'
    with optional modifiers after the pk, e.g.,
    '^<rsrc_collection_name>/<pk>/edit/$'.
    """
    if modifiers:
        return (r'^{rsrc_collection_name}/(?P<pk>{pk_patt})/'
                r'{modifiers}/$'.format(
                    rsrc_collection_name=rsrc_collection_name,
                    pk_patt=pk_patt,
                    modifiers='/'.join(modifiers)))
    return r'^{rsrc_collection_name}/(?P<pk>{pk_patt})/$'.format(
        rsrc_collection_name=rsrc_collection_name, pk_patt=pk_patt)


def yield_search_routes(rsrc_member_name):
    """Yield the ``Route()``s needed to configure search across the resource
    with member name ``rsrc_member_name``.
    """
    rsrc_collection_name = INFLP.plural(rsrc_member_name)
    class_name = rsrc_collection_name.capitalize()
    yield Route(name=rsrc_collection_name,
                regex=get_collection_targetting_regex(rsrc_collection_name),
                http_method='SEARCH',
                class_name=class_name,
                method_name='search')
    yield Route(name='{}_search'.format(rsrc_collection_name),
                regex=get_collection_targetting_regex(
                    rsrc_collection_name, modifiers=['search']),
                http_method='POST',
                class_name=class_name,
                method_name='search')
    yield Route(name='{}_new_search'.format(rsrc_collection_name),
                regex=get_collection_targetting_regex(
                    rsrc_collection_name, modifiers=['new_search']),
                http_method='GET',
                class_name=class_name,
                method_name='new_search')


def yield_standard_routes(rsrc_member_name, pk_patt=UUID_PATT):
    """Yield the ``Route()``s needed to configure standard CRUD actions on the
    resource with member name ``rsrc_member_name``.
    """
    rsrc_collection_name = INFLP.plural(rsrc_member_name)
    class_name = rsrc_collection_name.capitalize()
    for action in RESOURCE_ACTIONS:
        method_name = action
        http_method = ACTIONS2METHODS.get(action, DEFAULT_METHOD)
        if action in COLLECTION_TARGETTING:
            route_name = rsrc_collection_name
            regex = get_collection_targetting_regex(rsrc_collection_name)
        elif action in MEMBER_TARGETTING:
            route_name = rsrc_member_name
            regex = get_member_targetting_regex(rsrc_collection_name, pk_patt)
        elif action == 'new':
            route_name = '{}_new'.format(rsrc_collection_name)
            regex = get_collection_targetting_regex(
                rsrc_collection_name, modifiers=['new'])
        else:  # edit is default case
            route_name = '{}_edit'.format(rsrc_member_name)
            regex = get_member_targetting_regex(
                rsrc_collection_name, pk_patt, modifiers=['edit'])
        yield Route(name=route_name,
                    regex=regex,
                    http_method=http_method,
                    class_name=class_name,
                    method_name=method_name)


def method_not_allowed(tried_method, accepted_methods):
    return {'error': 'The {} method is not allowed for this resources. The'
                     ' accepted methods are: {}'.format(
                         tried_method, ', '.join(accepted_methods))}


route_builder = RouteBuilder()
route_builder.register_routes_for_resources(RESOURCES)
urlpatterns = route_builder.get_urlpatterns()
