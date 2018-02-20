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
    | Read specific   | GET         | /<cllctn_name>/<pk>      | show   |
    | Update specific | PUT         | /<cllctn_name>/<pk>      | update |
    | Update data     | GET         | /<cllctn_name>/<pk>/edit | edit   |
    | Delete specific | DELETE      | /<cllctn_name>/<pk>      | delete |
    | Search          | SEARCH      | /<cllctn_name>           | search |
    +-----------------+-------------+--------------------------+--------+

"""

import abc
import json
import logging

from django.conf import settings
from django.conf.urls import url
from django.forms.models import model_to_dict
from django.http import JsonResponse
from formencode.schema import Schema
from formencode.validators import Int
import inflect

import locations.models as models
from locations.api.v3.querybuilder import QueryBuilder
import locations.api.v3.schemata as schemata

LOGGER = logging.getLogger(__name__)

INFLP = inflect.engine()
INFLP.classical()


JSONDecodeErrorResponse = {
    'error': 'JSON decode error: the parameters provided were not valid'
             ' JSON.'
}

UNAUTHORIZED_MSG = {
    'error': 'You are not authorized to access this resource.'
}




class ReadonlyResources(object):
    """Super-class of ABC ``Resources`` and all read-only OLD resource views.
    RESTful CRUD(S) interface based on the Atom protocol:

    +-----------------+-------------+--------------------------+--------+
    | Purpose         | HTTP Method | Path                     | Method |
    +=================+=============+==========================+========+
    | Create new      | POST        | /<cllctn_name>           | create |
    +-----------------+-------------+--------------------------+--------+
    | Create data     | GET         | /<cllctn_name>/new       | new    |
    +-----------------+-------------+--------------------------+--------+
    | Read all        | GET         | /<cllctn_name>           | index  |
    +-----------------+-------------+--------------------------+--------+
    | Read specific   | GET         | /<cllctn_name>/<pk>      | show   |
    +-----------------+-------------+--------------------------+--------+
    | Update specific | PUT         | /<cllctn_name>/<pk>      | update |
    +-----------------+-------------+--------------------------+--------+
    | Update data     | GET         | /<cllctn_name>/<pk>/edit | edit   |
    +-----------------+-------------+--------------------------+--------+
    | Delete specific | DELETE      | /<cllctn_name>/<pk>      | delete |
    +-----------------+-------------+--------------------------+--------+
    | Search          | SEARCH      | /<cllctn_name>           | search |
    +-----------------+-------------+--------------------------+--------+

    Note: the create, new, update, edit, and delete actions are all exposed via
    the REST API; however, they invariably return 404 responses.
    """

    inflect_p = inflect.engine()
    inflect_p.classical()

    def __init__(self, request):
        self.request = request
        self._logged_in_user = None
        self._query_builder = None
        self.primary_key = 'uuid'
        # Names
        if not getattr(self, 'collection_name', None):
            self.collection_name = self.__class__.__name__.lower()
        if not getattr(self, 'hmn_collection_name', None):
            self.hmn_collection_name = self.collection_name
        if not getattr(self, 'member_name', None):
            self.member_name = self.inflect_p.singular_noun(
                self.collection_name)
        if not getattr(self, 'hmn_member_name', None):
            self.hmn_member_name = self.member_name
        if not getattr(self, 'model_name', None):
            self.model_name = self.member_name.capitalize()
        self.schema_cls_name = self.model_name + 'Schema'
        # Classes
        if not getattr(self, 'model_cls', None):
            self.model_cls = getattr(models, self.model_name)
        self.schema_cls = getattr(schemata, self.schema_cls_name, None)

    @property
    def query_builder(self):
        if not self._query_builder:
            self._query_builder = QueryBuilder(
                model_name=self.model_name,
                primary_key=self.primary_key,
                settings=settings
            )
        return self._query_builder

    @property
    def logged_in_user(self):
        """Property to access the logged in user. QUESTION: Is this a db
        model?
        """
        if not self._logged_in_user:
            self._logged_in_user = self.request.user
        return self._logged_in_user

    ###########################################################################
    # Public CRUD(S) Methods
    ###########################################################################

    def create(self):
        LOGGER.warning('Failed attempt to create a read-only %s',
                       self.hmn_member_name)
        return READONLY_RSLT, 404

    def new(self):
        LOGGER.warning('Failed attempt to get data for creating a read-only %s',
                       self.hmn_member_name)
        return READONLY_RSLT, 404

    def index(self):
        """Get all resources.

        - URL: ``GET /<resource_collection_name>`` with optional query string
          parameters for ordering and pagination.

        :returns: a JSON-serialized array of resources objects.
        """
        LOGGER.info('Attempting to read all %s', self.hmn_collection_name)
        query_set = self.model_cls.objects
        get_params = self.request.GET
        try:
            query_set = self.add_order_by(query_set, get_params)
            query_set = self._filter_query(query_set)
            result = add_pagination(query_set, get_params)
        except Invalid as error:
            errors = error.unpack_errors()
            LOGGER.warning('Attempt to read all %s resulted in an error(s): %s',
                           self.hmn_collection_name, errors)
            return {'errors': errors}, 400
        headers_ctl = self._headers_control(result)
        if headers_ctl is not False:
            return headers_ctl
        LOGGER.info('Reading all %s', self.hmn_collection_name)
        return result

    def show(self, pk):
        """Return a resource, given its pk.
        :URL: ``GET /<resource_collection_name>/<pk>``
        :param str pk: the ``pk`` value of the resource to be returned.
        :returns: a resource model object.
        """
        LOGGER.info('Attempting to read a single %s', self.hmn_member_name)
        resource_model = self._model_from_pk(pk)
        if not resource_model:
            msg = self._rsrc_not_exist(pk)
            LOGGER.warning(msg)
            return {'error': msg}, 404
        if self._model_access_unauth(resource_model) is not False:
            LOGGER.warning(UNAUTHORIZED_MSG)
            return UNAUTHORIZED_MSG, 403
        LOGGER.info('Reading a single %s', self.hmn_member_name)
        return self._get_show_dict(resource_model)

    def update(self, pk):
        LOGGER.warning('Failed attempt to update a read-only %s',
                       self.hmn_member_name)
        return READONLY_RSLT, 404

    def edit(self, pk):
        LOGGER.warning('Failed attempt to get data for updating a read-only %s',
                       self.hmn_member_name)
        return READONLY_RSLT, 404

    def delete(self, pk):
        LOGGER.warning('Failed attempt to delete a read-only %s',
                       self.hmn_member_name)
        return READONLY_RSLT, 404

    def search(self):
        """Return the list of resources matching the input JSON query.

        - URL: ``SEARCH /<resource_collection_name>`` (or
               ``POST /<resource_collection_name>/search``)
        - request body: A JSON object of the form::

              {"query": {"filter": [ ... ], "order_by": [ ... ]},
               "paginator": { ... }}

          where the ``order_by`` and ``paginator`` attributes are optional.
        """
        LOGGER.info('Attempting to search over %s', self.hmn_collection_name)
        try:
            python_search_params = json.loads(
                self.request.body.decode('utf8'))
        except ValueError:
            LOGGER.warning('Request body was not valid JSON')
            return JSONDecodeErrorResponse, 400
        try:
            query_set = self.query_builder.get_query_set(
                python_search_params.get('query'))
        except (OLDSearchParseError, Invalid) as error:
            errors = error.unpack_errors()
            LOGGER.warning(
                'Attempt to search over all %s resulted in an error(s): %s',
                self.hmn_collection_name, errors)
            return {'errors': errors}, 400
        # Might be better to catch (OperationalError, AttributeError,
        # InvalidRequestError, RuntimeError):
        except Exception as error:  # FIX: too general exception
            LOGGER.warning('Filter expression (%s) raised an unexpected'
                           ' exception: %s.', self.request.body, error)
            return {'error': 'The specified search parameters generated an'
                             ' invalid database query'}, 400
        query_set = self._eagerload_model(query_set)
        query_set = self._filter_query(query_set)
        try:
            ret = add_pagination(query_set, python_search_params.get('paginator'))
        except OperationalError:
            msg = ('The specified search parameters generated an invalid'
                   ' database query')
            LOGGER.warning(msg)
            return {'error': msg}, 400
        except Invalid as error:  # For paginator schema errors.
            errors = error.unpack_errors()
            LOGGER.warning(
                'Attempt to search over all %s resulted in an error(s): %s',
                self.hmn_collection_name, errors)
            return {'errors': errors}, 400
        else:
            LOGGER.info('Successful search over %s', self.hmn_collection_name)
            return ret

    def new_search(self):
        """Return the data necessary to search over this type of resource.

        - URL: ``GET /<resource_collection_name>/new_search``

        :returns: a JSON object with a ``search_parameters`` attribute which
         resolves to an object with attributes ``attributes`` and ``relations``.
        """
        LOGGER.info('Returning search parameters for %s', self.hmn_member_name)
        return {'search_parameters':
                self.query_builder.get_search_parameters()}


    ###########################################################################
    # Private Methods for Override: redefine in views for custom behaviour
    ###########################################################################

    def _get_show_dict(self, resource_model):
        """Return the model as a dict for the return value of a successful show
        request. This is indirected so that resources like collections can
        override and do special things.
        """
        try:
            return resource_model.get_dict()
        except AttributeError:
            return model_to_dict(resource_model)

    def _get_create_dict(self, resource_model):
        return self._get_show_dict(resource_model)

    def _get_edit_dict(self, resource_model):
        return self._get_show_dict(resource_model)

    def _get_update_dict(self, resource_model):
        return self._get_create_dict(resource_model)

    def _eagerload_model(self, query_set):
        """Override this in a subclass with model-specific eager loading."""
        return get_eagerloader(self.model_name)(query_set)

    def _filter_query(self, query_set):
        """Override this in a subclass with model-specific query filtering.
        E.g.,::
            >>> return filter_restricted_models(self.model_name, query_set)
        """
        return query_set

    def _headers_control(self, result):
        """Take actions based on header values and/or modify headers. If
        something other than ``False`` is returned, that will be the response.
        Useful for Last-Modified/If-Modified-Since caching, e.g., in ``index``
        method of Forms view.
        """
        return False

    def _update_unauth(self, resource_model):
        """Return ``True`` if update of the resource model cannot proceed."""
        return self._model_access_unauth(resource_model)

    def _update_unauth_msg_obj(self):
        """Return the dict that will be returned when ``self._update_unauth()``
        returns ``True``.
        """
        return UNAUTHORIZED_MSG

    def _model_access_unauth(self, resource_model):
        """Implement resource/model-specific access controls based on
        (un-)restricted(-ness) of the current logged in user and the resource
        in question. Return something other than ``False`` to trigger a 403
        response.
        """
        return False

    def _model_from_pk(self, pk):
        """Return a particular model instance, given the model pk."""
        return self.model_cls.objects.get(**{self.primary_key: pk})

    ###########################################################################
    # Utilities
    ###########################################################################

    def _rsrc_not_exist(self, pk):
        return 'There is no %s with %s %s' % (self.hmn_member_name,
                                              self.primary_key, pk)

    def add_order_by(self, query_set, order_by_params, query_builder=None):
        """Add an ORDER BY clause to the query_set using the ``get_order_bys``
        method of the instance's query_builder (if possible) or using a default
        ORDER BY <self.primary_key> ASC.
        """
        if not query_builder:
            query_builder = self.query_builder
        if not order_by_params:
            inp_order_bys = None
        inp_order_bys = [list(filter(
            None, [order_by_params.get('order_by_attribute'),
                   order_by_params.get('order_by_subattribute'),
                   order_by_params.get('order_by_direction')]))]
        return query_set.order_by(
            query_builder.get_order_bys(inp_order_bys, primary_key=self.primary_key))


class Resources(ReadonlyResources):
    """Abstract base class for all (modifiable) resource views. RESTful
    CRUD(S) interface:

    +-----------------+-------------+--------------------------+--------+
    | Purpose         | HTTP Method | Path                     | Method |
    +-----------------+-------------+--------------------------+--------+
    | Create new      | POST        | /<cllctn_name>           | create |
    | Create data     | GET         | /<cllctn_name>/new       | new    |
    | Read all        | GET         | /<cllctn_name>           | index  |
    | Read specific   | GET         | /<cllctn_name>/<pk>      | show   |
    | Update specific | PUT         | /<cllctn_name>/<pk>      | update |
    | Update data     | GET         | /<cllctn_name>/<pk>/edit | edit   |
    | Delete specific | DELETE      | /<cllctn_name>/<pk>      | delete |
    | Search          | SEARCH      | /<cllctn_name>           | search |
    +-----------------+-------------+--------------------------+--------+
    """

    __metaclass__ = abc.ABCMeta

    ###########################################################################
    # Abstract Methods --- must be defined in subclasses
    ###########################################################################

    @abc.abstractmethod
    def _get_user_data(self, data):
        """Process the user-provided ``data`` dict, crucially returning a *new*
        dict created from it which is ready for construction of a resource
        model.
        """

    @abc.abstractmethod
    def _get_create_data(self, data):
        """Generate a dict representing a resource to be created; add any
        creation-specific data. The ``data`` dict should be expected to be
        unprocessed, i.e., a JSON-decoded dict from the request body.
        """

    @abc.abstractmethod
    def _get_update_data(self, user_data):
        """Generate a dict representing the updated state of a specific
        resource. The ``user_data`` dict should be expected to be the output of
        ``self._get_user_data(data)``.
        """

    ###########################################################################
    # Public CRUD(S) Methods
    ###########################################################################

    def create(self):
        """Create a new resource and return it.
        :URL: ``POST /<resource_collection_name>``
        :request body: JSON object representing the resource to create.
        :returns: the newly created resource.
        """
        LOGGER.info('Attempting to create a new %s.', self.hmn_member_name)
        schema = self.schema_cls()
        try:
            values = json.loads(self.request.body.decode('utf8'))
        except ValueError:
            self.request.response.status_int = 400
            LOGGER.warning('Request body was not valid JSON')
            return JSONDecodeErrorResponse
        state = self._get_create_state(values)
        try:
            data = schema.to_python(values, state)
        except Invalid as error:
            self.request.response.status_int = 400
            errors = error.unpack_errors()
            LOGGER.warning(
                'Attempt to create a(n) %s resulted in an error(s): %s',
                self.hmn_member_name, errors)
            return {'errors': errors}
        resource = self._create_new_resource(data)
        self.request.dbsession.add(resource)
        self.request.dbsession.flush()
        self._post_create(resource)
        LOGGER.info('Created a new %s.', self.hmn_member_name)
        return self._get_create_dict(resource)

    def new(self):
        """Return the data necessary to create a new resource.

        - URL: ``GET /<resource_collection_name>/new``.

        :returns: a dict containing the related resources necessary to create a
         new resource of this type.

        .. note:: See :func:`_get_new_edit_data` to understand how the query
           string parameters can affect the contents of the lists in the
           returned dictionary.
        """
        LOGGER.info('Returning the data needed to create a new %s.',
                    self.hmn_member_name)
        return self._get_new_edit_data(self.request.GET)

    def update(self, pk):
        """Update a resource and return it.

        - URL: ``PUT /<resource_collection_name>/<pk>``
        - Request body: JSON object representing the resource with updated
          attribute values.

        :param str pk: the ``pk`` value of the resource to be updated.
        :returns: the updated resource model.
        """
        resource_model = self._model_from_pk(pk)
        LOGGER.info('Attempting to update %s %s.', self.hmn_member_name, pk)
        if not resource_model:
            self.request.response.status_int = 404
            msg = self._rsrc_not_exist(pk)
            LOGGER.warning(msg)
            return {'error': msg}
        if self._update_unauth(resource_model) is not False:
            self.request.response.status_int = 403
            msg = self._update_unauth_msg_obj()
            LOGGER.warning(msg)
            return msg
        schema = self.schema_cls()
        try:
            values = json.loads(self.request.body.decode('utf8'))
        except ValueError:
            self.request.response.status_int = 400
            LOGGER.warning(JSONDecodeErrorResponse)
            return JSONDecodeErrorResponse
        state = self._get_update_state(values, pk, resource_model)
        try:
            data = schema.to_python(values, state)
        except Invalid as error:
            self.request.response.status_int = 400
            errors = error.unpack_errors()
            LOGGER.warning(errors)
            return {'errors': errors}
        resource_dict = resource_model.get_dict()
        resource_model = self._update_resource_model(resource_model, data)
        # resource_model will be False if there are no changes
        if not resource_model:
            self.request.response.status_int = 400
            msg = ('The update request failed because the submitted data were'
                   ' not new.')
            LOGGER.warning(msg)
            return {'error': msg}
        self._backup_resource(resource_dict)
        self.request.dbsession.add(resource_model)
        self.request.dbsession.flush()
        self._post_update(resource_model, resource_dict)
        LOGGER.info('Updated %s %s.', self.hmn_member_name, pk)
        return self._get_update_dict(resource_model)

    def edit(self, pk):
        """Return a resource and the data needed to update it.
        :URL: ``GET /<resource_collection_name>/edit``
        :param str pk: the ``pk`` value of the resource that will be updated.
        :returns: a dictionary of the form::

                {"<resource_member_name>": {...}, "data": {...}}

            where the value of the ``<resource_member_name>`` key is a
            dictionary representation of the resource and the value of the
            ``data`` key is a dictionary containing the data needed to edit an
            existing resource of this type.
        """
        resource_model = self._model_from_pk(pk)
        LOGGER.info('Attempting to return the data needed to update %s %s.',
                    self.hmn_member_name, pk)
        if not resource_model:
            self.request.response.status_int = 404
            msg = self._rsrc_not_exist(pk)
            LOGGER.warning(msg)
            return {'error': msg}
        if self._model_access_unauth(resource_model) is not False:
            self.request.response.status_int = 403
            LOGGER.warning('User not authorized to access edit action on model')
            return UNAUTHORIZED_MSG
        LOGGER.info('Returned the data needed to update %s %s.',
                    self.hmn_member_name, pk)
        return {
            'data': self._get_new_edit_data(self.request.GET),
            self.member_name: self._get_edit_dict(resource_model)
        }

    def delete(self, pk):
        """Delete an existing resource and return it.
        :URL: ``DELETE /<resource_collection_name>/<pk>``
        :param str pk: the ``pk`` value of the resource to be deleted.
        :returns: the deleted resource model.
        """
        resource_model = self._model_from_pk(pk)
        LOGGER.info('Attempting to delete %s %s.', self.hmn_member_name, pk)
        if not resource_model:
            self.request.response.status_int = 404
            msg = self._rsrc_not_exist(pk)
            LOGGER.warning(msg)
            return {'error': msg}
        if self._delete_unauth(resource_model) is not False:
            self.request.response.status_int = 403
            msg = self._delete_unauth_msg_obj()
            LOGGER.warning(msg)
            return msg
        error_msg = self._delete_impossible(resource_model)
        if error_msg:
            self.request.response.status_int = 403
            LOGGER.warning(error_msg)
            return {'error': error_msg}
        # Not all Pylons controllers were doing this:
        resource_model.modifier = self.logged_in_user
        resource_dict = self._get_delete_dict(resource_model)
        self._backup_resource(resource_dict)
        self._pre_delete(resource_model)
        self.request.dbsession.delete(resource_model)
        self.request.dbsession.flush()
        self._post_delete(resource_model)
        LOGGER.info('Deleted %s %s.', self.hmn_member_name, pk)
        return resource_dict

    ###########################################################################
    # Additional Resource Actions --- Common to a subset of resources
    ###########################################################################

    def history(self):
        """Return the resource with the pk in the path along with its previous
        versions.

        :URL: ``GET /<resource_collection_name>/history/<pk>``
        :param str pk: a string matching the ``pk`` or ``UUID`` value of the
            resource whose history is requested.
        :returns: A dictionary of the form::

                {"<resource_member_name>": { ... },
                 "previous_versions": [ ... ]}

            where the value of the ``<resource_member_name>`` key is the
            resource whose history is requested and the value of the
            ``previous_versions`` key is a list of dictionaries representing
            previous versions of the resource.
        """
        pk = self.request.matchdict['pk']
        LOGGER.info('Requesting history of %s %s.', self.hmn_member_name, pk)
        resource_model, previous_versions = self.db\
            .get_model_and_previous_versions(self.model_name, pk)
        if resource_model or previous_versions:
            unrestricted_previous_versions = [
                pv for pv in previous_versions
                if not self._model_access_unauth(pv)]
            resource_restricted = (resource_model and
                                   self._model_access_unauth(resource_model))
            prev_vers_restricted = (
                previous_versions and not unrestricted_previous_versions)
            if resource_restricted or prev_vers_restricted :
                self.request.response.status_int = 403
                LOGGER.warning(UNAUTHORIZED_MSG)
                return UNAUTHORIZED_MSG
            LOGGER.info('Returned history of %s %s.', self.hmn_member_name, pk)
            return {self.member_name: resource_model,
                    'previous_versions': unrestricted_previous_versions}
        self.request.response.status_int = 404
        msg = 'No %s or %s backups match %s' % (
            self.hmn_collection_name, self.hmn_member_name, pk)
        LOGGER.warning(msg)
        return {'error': msg}

    ###########################################################################
    # Private methods for write-able resources
    ###########################################################################

    def _delete_unauth(self, resource_model):
        """Implement resource/model-specific controls over delete requests.
        Return something other than ``False`` to trigger a 403 response.
        """
        return False

    def _delete_unauth_msg_obj(self):
        """Return the dict that will be returned when ``self._delete_unauth()``
        returns ``True``.
        """
        return UNAUTHORIZED_MSG

    def _get_delete_dict(self, resource_model):
        """Override this in sub-classes for special resource dict creation."""
        return resource_model.get_dict()

    def _pre_delete(self, resource_model):
        """Perform actions prior to deleting ``resource_model`` from the
        database.
        """
        pass

    def _post_delete(self, resource_model):
        """Perform actions after deleting ``resource_model`` from the
        database.
        """
        pass

    def _delete_impossible(self, resource_model):
        """Return something other than false in a sub-class if this particular
        resource model cannot be deleted.
        """
        return False

    def _backup_resource(self, resource_dict):
        """Perform a backup of the provided ``resource_dict``, if applicable."""
        pass

    def _create_new_resource(self, data):
        """Create a new resource.
        :param dict data: the data for the resource to be created.
        :returns: an SQLAlchemy model object representing the resource.
        """
        return self.model_cls(**self._get_create_data(data))

    def _post_create(self, resource_model):
        """Perform some action after creating a new resource model in the
        database. E.g., with forms we have to update all of the forms that
        contain the newly entered form as a morpheme.
        """
        pass

    def _get_create_state(self, values):
        """Return a SchemaState instance for validation of the resource during
        a create request.
        """
        return SchemaState(
            full_dict=values,
            db=self.db,
            logged_in_user=self.logged_in_user)

    def _get_update_state(self, values, pk, resource_model):
        update_state = self._get_create_state(values)
        update_state.pk = pk
        return update_state

    def _update_resource_model(self, resource_model, data):
        """Update ``resource_model`` with ``data`` and return something other
        than ``False`` if resource_model has changed as a result.
        :param resource_model: the resource model to be updated.
        :param dict data: user-supplied representation of the updated resource.
        :returns: the updated resource model or, if ``changed`` has not been set
            to ``True``, ``False``.
        """
        changed = False
        user_data = self._get_user_data(data)
        for attr, val in user_data.items():
            if self._distinct(attr, val, getattr(resource_model, attr)):
                changed = True
                break
        if changed:
            for attr, val in self._get_update_data(user_data).items():
                setattr(resource_model, attr, val)
            return resource_model
        return changed

    def _distinct(self, attr, new_val, existing_val):
        """Return true if ``new_val`` is distinct from ``existing_val``. The
        ``attr`` value is provided so that certain attributes (e.g., m2m) can
        have a special definition of "distinct".
        """
        return new_val != existing_val

    def _post_update(self, resource_model, previous_resource_dict):
        """Perform some action after updating an existing resource model in the
        database. E.g., with forms we have to update all of the forms that
        contain the newly entered form as a morpheme.
        """
        pass

    def _get_new_edit_data(self, get_params):
        """Return a dict containing the data (related models) necessary to
        create a new (or edit an existing) resource model of the given type.
        :param get_params: the ``request.GET`` dictionary-like object generated
            by Pylons which contains the query string parameters of the request.
        :returns: A dictionary whose values are lists of objects needed to
            create or update forms.
        If ``get_params`` has no keys, then return all collections encoded in
        ``self._get_new_edit_collections()``. If ``get_params`` does have keys,
        then for each key whose value is a non-empty string (and not a valid
        ISO 8601 datetime) add the appropriate list of objects to the return
        dictionary. If the value of a key is a valid ISO 8601 datetime string,
        add the corresponding list of objects *only* if the datetime does *not*
        match the most recent ``datetime_modified`` value of the resource.
        That is, a non-matching datetime indicates that the requester has
        out-of-date data.
        """
        result = {}
        for collection in self._get_new_edit_collections():
            result[collection] = []
            rescol = self.resource_collections[collection]
            if (    collection in self._get_mandatory_collections() or
                    not rescol.model_name):
                result[collection] = rescol.getter()
            # There are GET params, so we are selective in what we return.
            elif get_params:
                val = get_params.get(collection)
                # Proceed so long as val is not an empty string.
                if val:
                    val_as_datetime_obj = h.datetime_string2datetime(val)
                    if val_as_datetime_obj:
                        # Value of param is an ISO 8601 datetime string that
                        # does not match the most recent datetime_modified of
                        # the relevant model in the db: therefore we return a
                        # list of objects/dicts. If the datetimes do match,
                        # this indicates that the requester's own stores are
                        # up-to-date so we return nothing.
                        if (val_as_datetime_obj !=
                                self.db.get_most_recent_modification_datetime(
                                    rescol.model_name)):
                            result[collection] = rescol.getter()
                    else:
                        result[collection] = rescol.getter()
            # There are no GET params, so we get everything from the db and
            # return it.
            else:
                #for collection, rescol in self.resource_collections.items():
                #    result[collection] = rescol.getter()
                result[collection] = rescol.getter()
        return dict(result)

    # Map resource collection names to ``ResCol`` instances containing the name
    # of the relevant model and a function that gets all instances of the
    # relevant resource collection.
    @property
    def resource_collections(self):
        return {
            'allowed_file_types': ResCol(
                '',
                lambda: ALLOWED_FILE_TYPES),
            'collection_types': ResCol(
                '',
                lambda: COLLECTION_TYPES),
            'corpora': ResCol(
                'Corpus',
                self.db.get_mini_dicts_getter('Corpus')),
            'corpus_formats': ResCol(
                '',
                lambda: list(CORPUS_FORMATS.keys())),
            'elicitation_methods': ResCol(
                'ElicitationMethod',
                self.db.get_mini_dicts_getter('ElicitationMethod')),
            'form_searches': ResCol(
                'FormSearch',
                self.db.get_mini_dicts_getter('FormSearch')),
            'grammaticalities': ResCol(
                'ApplicationSettings',
                self.db.get_grammaticalities),
            'languages': ResCol(
                'Language',
                self.db.get_languages),
            'markup_languages': ResCol(
                '',
                lambda: MARKUP_LANGUAGES),
            'morphologies': ResCol(
                'Morphology',
                self.db.get_mini_dicts_getter('Morphology')),
            'morpheme_language_models': ResCol(
                'MorphemeLanguageModel',
                self.db.get_mini_dicts_getter('MorphemeLanguageModel')),
            'orthographies': ResCol(
                'Orthography',
                self.db.get_mini_dicts_getter('Orthography')),
            'phonologies': ResCol(
                'Phonology',
                self.db.get_mini_dicts_getter('Phonology')),
            'roles': ResCol(
                '',
                lambda: USER_ROLES),
            'sources': ResCol(
                'Source',
                self.db.get_mini_dicts_getter('Source')),
            'speakers': ResCol(
                'Speaker',
                self.db.get_mini_dicts_getter('Speaker')),
            'syntactic_categories': ResCol(
                'SyntacticCategory',
                self.db.get_mini_dicts_getter('SyntacticCategory')),
            'syntactic_category_types': ResCol(
                '',
                lambda: SYNTACTIC_CATEGORY_TYPES),
            'tags': ResCol(
                'Tag',
                self.db.get_mini_dicts_getter('Tag')),
            'toolkits': ResCol(
                '',
                lambda: LANGUAGE_MODEL_TOOLKITS),
            'types': ResCol(  # BibTeX entry types
                '',
                lambda: list(ENTRY_TYPES.keys())),
            'users': ResCol(
                'User',
                self.db.get_mini_dicts_getter('User')),
            'utterance_types': ResCol(
                '',
                lambda: UTTERANCE_TYPES)
        }

    def _get_new_edit_collections(self):
        """Return a sequence of strings representing the names of the
        collections (typically resource collections) that are required in order
        to create a new, or edit an existing, resource of the given type. For
        many resources, an empty typle is fine, but for others an override
        returning a tuple of collection names from the keys of
        ``self.resource_collections`` will be required.
        """
        return ()

    def _get_mandatory_collections(self):
        """Return a subset of the return value of
        ``self._get_new_edit_collections`` indicating those collections that
        should always be returned in their entirety.
        """
        return ()


class SchemaState:
    """Empty class used to create a state instance with a 'full_dict' attribute
    that points to a dict of values being validated by a schema. For example,
    the call to FormSchema().to_python requires this State() instance as its
    second argument in order to make the inventory-based validators work
    correctly (see, e.g., ValidOrthographicTranscription).
    """

    def __init__(self, full_dict=None, db=None, logged_in_user=None,
                 **kwargs):
        """Return a State instance with some special attributes needed in the
        forms and oldcollections controllers.
        """
        self.full_dict = full_dict
        self.db = db
        self.user = logged_in_user
        for key, val in kwargs.items():
            setattr(self, key, val)


def get_eagerloader(model_name):
    return lambda query_set: query_set


class PaginatorSchema(Schema):
    allow_extra_fields = True
    filter_extra_fields = False
    items_per_page = Int(not_empty=True, min=1)
    page = Int(not_empty=True, min=1)


def _get_start_and_end_from_paginator(paginator):
    start = (paginator['page'] - 1) * paginator['items_per_page']
    return (start, start + paginator['items_per_page'])


def get_paginated_query_results(query_set, paginator):
    if 'count' not in paginator:
        paginator['count'] = query_set.count()
    start, end = _get_start_and_end_from_paginator(paginator)
    return {
        'paginator': paginator,
        'items': query_set[start:end]
    }


def add_pagination(query_set, paginator):
    if (paginator and paginator.get('page') is not None and
            paginator.get('items_per_page') is not None):
        # raises formencode.Invalid if paginator is invalid
        paginator = PaginatorSchema.to_python(paginator)
        return get_paginated_query_results(query_set, paginator)
    return query_set



class Packages(Resources):

    def _get_user_data(self, data):
        return {
            'description': h.normalize(data['description']),
        }

    def _get_create_data(self, data):
        return self._get_update_data(self._get_user_data(data))

    def _get_update_data(self, user_data):
        user_data.update({})
        return user_data


class Pipelines(Resources):

    def _get_user_data(self, data):
        return {
            'description': h.normalize(data['description']),
        }

    def _get_create_data(self, data):
        return self._get_update_data(self._get_user_data(data))

    def _get_update_data(self, user_data):
        user_data.update({})
        return user_data
