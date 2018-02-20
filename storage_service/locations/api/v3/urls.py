"""Version 3 of the Storage Service API.

The Storage Service exposes the following resources via an HTTP JSON interface:

- /fsobjects/ --- directories and files on disk, read-only, no database models
- /locations/ --- purpose-specific paths within a /spaces/ resource
- /packages/ --- Information Package (SIP, DIP or AIP)
- /spaces/ --- storage space with behaviour specific to backing system
- /pipelines/ --- an Archivematica instance that is the source of a package
- /file/ --- a file on disk (which is in a package), represented as db row.

All resources have endpoints that follow this pattern::

    +-----------------+-------------+--------------------------+--------+
    | Purpose         | HTTP Method | Path                     | Method |
    +-----------------+-------------+--------------------------+--------+
    | Create new      | POST        | /<cllctn_name>           | create |
    | Create data     | GET         | /<cllctn_name>/new       | new    |
    | Read all        | GET         | /<cllctn_name>           | index  |
    | Read specific   | GET         | /<cllctn_name>/<id>      | show   |
    | Update specific | PUT         | /<cllctn_name>/<id>      | update |
    | Update data     | GET         | /<cllctn_name>/<id>/edit | edit   |
    | Delete specific | DELETE      | /<cllctn_name>/<id>      | delete |
    | Search          | SEARCH      | /<cllctn_name>           | search |
    +-----------------+-------------+--------------------------+--------+

"""

from functools import partial
import logging
import string

from django.conf import settings
from django.conf.urls import url
from django.http import JsonResponse
import inflect

import locations.api.v3.resources as resources

LOGGER = logging.getLogger(__name__)
UUID_PATT = r'\w{8}-\w{4}-\w{4}-\w{4}-\w{12}'
ROUTE_NAME_CHARS = string.letters + '_'
INFLP = inflect.engine()
INFLP.classical()

# ==============================================================================
#   Resource Routing Configuration
# ==============================================================================

# This dict is configuration for the resources that the SS exposes. All of the
# keys correspond to resources that will receive the standard methods/actions
# (create, new, index, show, update, edit, and delete). The config dict value
# signals additional actions on the resource, e.g., search or history. See the
# ``add_resource`` function for implementation.
RESOURCES = {
    'fsobject': {},
    'location': {},
    'package': {},
    'space': {},
    'pipeline': {},
    'file': {}
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
ACTIONS2METHODS = {'create': 'POST',
                   'delete': 'DELETE',
                   'update': 'PUT'}


class Dispatcher(object):

    def __init__(self):
        self.routes = {}

    def add_route(self, path, route_name, http_method, class_name, method_name):
        config = self.routes.get(path, {})
        config['route_name'] = route_name
        http_method_config = config.get('http_method', {})
        http_method_config[http_method] = (class_name, method_name)
        config['http_method'] = http_method_config
        self.routes[path] = config

    def get_urls(self):
        urls = []
        import pprint
        pprint.pprint(self.routes)
        for path, config in self.routes.items():
            route_name = config['route_name']
            def resource_callable(config, request, *args, **kwargs):
                try:
                    class_name, method_name = config['http_method'][request.method]
                except KeyError:
                    return JsonResponse(
                        method_not_allowed(request.method, list(config.keys())),
                        status=405)

                print('resource callable called')
                print('class_name')
                print(class_name)
                print('method_name')
                print(method_name)
                print('http_method')
                print(request.method)

                resource_cls = getattr(resources, class_name)
                instance = resource_cls(request)
                method = getattr(instance, method_name)
                print('args to method')
                print(args)
                print('kwargs to method')
                print(kwargs)
                #response = method(*args, **kwargs)
                response = method(**kwargs)
                try:
                    response, status = response
                except (ValueError, TypeError):
                    status = 200
                return JsonResponse(response, status=status)
            f = partial(resource_callable, config)
            urls.append(url(path, view=f, name=route_name))
        return urls


def register_resource_url_patterns(dispatcher, resources, pk_patt=UUID_PATT):
    """Yield all of the Django ``url`` instances implicit in the ``RESOURCES``
    configuration above.
    """
    for member_name, rsrc_config in resources.items():
        register_resource_urls(dispatcher, member_name, rsrc_config,
                               pk_patt=pk_patt)


def get_search_config(collection_name):
    """Return the route name, path, request method, and instance method name for
    configuring search across the resource with collection name
    ``collection_name``.
    """
    return (
        ('{}_search'.format(collection_name),
         r'^{}/$'.format(collection_name),
         'SEARCH',
         'search'),
        ('{}_search_post'.format(collection_name),
         r'^{}/search/$'.format(collection_name),
         'POST',
         'search'),
        ('{}_new_search'.format(collection_name),
         r'^{}/new_search/$'.format(collection_name),
         'GET',
         'new_search')
    )


def get_resource_callable(resource_cls_name, method_name, http_method):
    """Return a callable that works as a Django view: it takes a request and
    possibly other args and returns a Django JSON response. It assumes that the
    response is generated by instantiating ``resource_cls_name`` with the
    request object and calling its ``method_name`` with any remaining args.
    """
    def resource_callable(request, *args, **kwargs):
        instance = resource_cls_name(request)
        method = getattr(instance, method_name)
        response = method(*args, **kwargs)
        try:
            response, status = response
        except ValueError:
            status = 200
        return JsonResponse(response, status=status)
    return resource_callable


def method_not_allowed(tried_method, accepted_methods):
    return {'error': 'The {} method is not allowed for this resources. The'
            ' accepted methods are: {}'.format(
                tried_method, ', '.join(accepted_methods))}


def register_resource_urls(dispatcher, member_name, rsrc_config,
                           pk_patt=UUID_PATT):
    """Yield all of the Django ``url`` instances needed to expose resource
    ``member_name`` as a RESTful resource. The ``rsrc_config`` dict provides
    additional configuration of the resource; viz., setting 'searchable' to
    ``True`` will set up search-related routes. Configuration should be
    centralized in the ``RESOURCES`` global constant.
    """
    collection_name = INFLP.plural(member_name)
    class_name = collection_name.capitalize()
    # Search-related routes
    if rsrc_config.get('searchable', True):
        for route_name, path, http_method, method_name in get_search_config(
                collection_name):
            dispatcher.add_route(path, route_name, http_method, class_name,
                                 method_name)
    # Standard CRUD routes
    for action in RESOURCE_ACTIONS:
        method_name = action
        http_method = ACTIONS2METHODS.get(action, 'GET')
        if action in COLLECTION_TARGETTING:
            route_name = collection_name
            path = r'^{collection_name}/$'.format(collection_name=collection_name)
        elif action in MEMBER_TARGETTING:
            route_name = member_name
            path = (r'^{collection_name}/'
                    r'(?P<pk>{pk_patt})/$'.format(
                        collection_name=collection_name,
                        pk_patt=pk_patt))
        elif action == 'new':
            route_name = '{}_new'.format(member_name)
            path = r'^{collection_name}/new/$'.format(
                collection_name=collection_name)
        elif action == 'edit':
            route_name = '{}_edit'.format(member_name)
            path = (r'^{collection_name}/'
                    r'(?P<pk>{pk_patt})/edit/$'.format(
                        collection_name=collection_name,
                        pk_patt=pk_patt))
        dispatcher.add_route(path, route_name, http_method, class_name,
                             method_name)


dispatcher = Dispatcher()
register_resource_url_patterns(dispatcher, RESOURCES)
urlpatterns = dispatcher.get_urls()
