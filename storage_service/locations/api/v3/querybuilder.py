"""This module defines :class:`QueryBuilder`. A ``QueryBuilder`` instance is
used to build a query object from a native Python data structure: nested lists.

The sole public method is ``get_query_set``. It takes a dict with ``'filter'``
and ``'order_by'`` keys. The filter key evaluates to a filter expression
represented as a Python list (or JSON array). The ``get_query_set`` method
returns a Django QuerySet instance. Errors in the Python filter expression will
cause custom ``SearchParseError``s to be raised.

The searchable models and their attributes (scalars & collections) are defined
in QueryBuilder.schema.

Simple filter expressions are lists with four or five items. Complex filter
expressions are constructed via lists whose first element is one of the boolean
keywords 'and', 'or', 'not' and whose second element is a filter expression or
a list thereof (in the case of 'and' and 'or'). The examples below show a
filter expression accepted by ``QueryBuilder('Package').get_query_expression``
on the first line followed by the equivalent Django Q expression. Note that
the target model of the QueryBuilder defaults to 'Package' so all queries will
target the Package model by default.

1. Queries on scalar attributes::

        >>> ['Package', 'description', 'like', '%a%']
        >>> Q(description__contains='%a%')

2. Queries on scalar attributes of related resources::

        >>> ['Package', 'origin_pipeline', 'description', 'regex', '^[JS]']
        >>> Q(origin_pipeline__description__regex='^[JS]')

3. Queries based on the presence/absence of a related resource::

        >>> ['Package', 'origin_pipeline', '=', None]
        >>> Q(origin_pipeline__isnull==True)
        >>> ['Package', 'origin_pipeline', '!=', None]
        >>> Q(origin_pipeline__isnull==False)

4. Queries over scalar attributes of related collections of resources::

        >>> ['Package', 'replicas', 'uuid', 'in', [uuid1, uuid2]]
        >>> Q(replicas__uuid__in=[uuid1, uuid2])))

5. Negation::

        >>> ['not', ['Package', 'description', 'like', '%a%']]
        >>> ~Q(description__contains='%a%')

6. Conjunction::

        >>> ['and', [['Package', 'description', 'like', '%a%'],
        >>>          ['Package', 'origin_pipeline', 'description', '=',
        >>>           'Well described.']]]
        >>> (Q(description__contains='%a%') &
        >>>  Q(origin_pipeline__description='Well described.'))

7. Disjunction::

        >>> ['or', [['Package', 'description', 'like', '%a%'],
        >>>         ['Package', 'origin_pipeline', 'description', '=',
        >>>          'Well described.']]]
        >>> (Q(description__contains='%a%') |
        >>>  Q(origin_pipeline__description='Well described.'))

8. Complex hierarchy of filters::

        >>> ['and', [['Package', 'description', 'like', '%a%'],
        >>>          ['not', ['Package', 'description', 'like', 'T%']],
        >>>          ['or', [['Package', 'size', '<', 1000],
        >>>                  ['Package', 'size', '>', 512]]]]]
        >>> (Q(description__contains='%a%') &
        >>>  ~Q(description__contains='T%') &
        >>>  (Q(size__lt=1000) | Q(size__gt=512)))
"""

import functools
import logging
import operator

from django.db.models import Q

import locations.models as models
import locations.api.v3.utils as utils


LOGGER = logging.getLogger(__name__)


class SearchParseError(Exception):

    def __init__(self, errors):
        self.errors = errors
        super(SearchParseError, self).__init__()

    def __repr__(self):
        return '; '.join(['%s: %s' % (k, self.errors[k]) for k in self.errors])

    def __str__(self):
        return self.__repr__()

    def unpack_errors(self):
        return self.errors


class QueryBuilder(object):
    """Generate a query object from a Python dictionary.

    Builds Django ORM queries from Python data structures representing
    arbitrarily complex filter expressions. Example usage::

        query_builder = QueryBuilder(model_name='Package')
        python_query = {'filter': [
            'and', [
                ['Package', 'description', 'like', '%a%'],
                ['not', ['Package', 'description', 'regex', '^[Tt]he']],
                ['or', [
                    ['Package', 'size', '<', 1000],
                    ['Package', 'size', '>', 512]]]]]}
        query_set = query_builder.get_query_set(python_query)
    """

    def __init__(self, model_name='Package', primary_key='uuid',
                 settings=None):
        self.errors = {}
        # The name of the target model, i.e., the one we are querying, e.g.,
        # 'Package'
        self.model_name = model_name
        # Some models have a primary key other than 'uuid' ...
        self.primary_key = primary_key
        if not settings:
            settings = {}

    def get_query_set(self, query_as_dict):
        self.clear_errors()
        query_expression = self._get_query_expression(query_as_dict.get('filter'))
        order_bys = self._get_order_bys(
            query_as_dict.get('order_by'), self.primary_key)
        self._raise_search_parse_error_if_necessary()
        query_set = self._get_model_manager()
        query_set = query_set.filter(query_expression)
        query_set = query_set.order_by(*order_bys)
        return query_set

    def get_query_expression(self, query_as_list):
        """The public method clears the errors and then calls the corresponding
        private method. This prevents interference from errors generated by
        previous order_by calls.
        """
        self.clear_errors()
        return self._get_query_expression(query_as_list)

    def _get_query_expression(self, query_as_list):
        """Return the filter expression generable by the input Python
        data structure or raise an SearchParseError if the data structure is
        invalid.
        """
        return self._python2queryexpr(query_as_list)

    def get_order_bys(self, inp_order_bys, primary_key='uuid'):
        """The public method clears the errors and then calls the private method.
        This prevents interference from errors generated by previous order_by calls.
        """
        self.clear_errors()
        return self._get_order_bys(inp_order_bys, primary_key)

    def _get_order_bys(self, inp_order_bys, primary_key='uuid'):
        """Input is a list of lists of the form [<attribute>], [<attribute>,
        <direction>] or [<related_attribute>, <attribute>, <direction>];
        output is a list of strings that is an acceptable argument to the
        Django ORM's ``order_by`` method.
        """
        default_order_bys = [primary_key]
        if inp_order_bys is None:
            return default_order_bys
        order_bys = []
        related_attribute_name = None
        for inp_order_by in inp_order_bys:
            if not isinstance(inp_order_by, list):
                self._add_to_errors(
                    str(inp_order_by), 'Order by elements must be lists')
                continue
            if len(inp_order_by) == 1:
                related_attribute_name, attribute_name, direction = (
                    None, inp_order_by[0], '')
            elif len(inp_order_by) == 2:
                related_attribute_name, attribute_name, direction = (
                    None, inp_order_by[0], inp_order_by[1])
            elif len(inp_order_by) == 3:
                related_attribute_name, attribute_name, direction = inp_order_by
            else:
                self._add_to_errors(
                    str(inp_order_by),
                    'Order by elements must be lists of 1, 2 or 3 elements')
                continue
            if related_attribute_name:
                related_model_name = self._get_attribute_model_name(
                    related_attribute_name, self.model_name)
                attribute_name = self._get_attribute_name(
                    attribute_name, related_model_name)
                related_attribute_name = self._get_attribute_name(
                    related_attribute_name, self.model_name)
                order_by = '{}__{}'.format(
                    related_attribute_name, attribute_name)
            else:
                order_by = self._get_attribute_name(
                    attribute_name, self.model_name)
            try:
                direction = self.order_by_directions[direction.lower()].get(
                    'alias', direction)
            except KeyError:
                self._add_to_errors(
                    direction, 'Unrecognized order by direction')
                continue
            order_bys.append(direction + order_by)
        return order_bys

    def clear_errors(self):
        self.errors = {}

    def _raise_search_parse_error_if_necessary(self):
        if self.errors:
            errors = self.errors.copy()
            # Clear the errors so the instance can be reused to build further
            # queries
            self.clear_errors()
            raise SearchParseError(errors)

    def _get_model_manager(self):
        query_model = getattr(models, self.model_name)
        return query_model.objects

    def _python2queryexpr(self, query_as_list):
        """This is the function that is called recursively (if necessary) to
        build the filter expression.
        """
        try:
            if query_as_list[0] in ('and', 'or'):
                op = {'and': operator.and_, 'or': operator.or_}[query_as_list[0]]
                return functools.reduce(
                    op, [self._python2queryexpr(x) for x in query_as_list[1]])
            if query_as_list[0] == 'not':
                return ~(self._python2queryexpr(query_as_list[1]))
            return self._get_simple_Q_expression(*query_as_list)
        except (TypeError, IndexError, AttributeError) as exc:
            self.errors['Malformed query error'] = 'The submitted query was malformed'
            self.errors[str(type(exc))] = str(exc)

    def _add_to_errors(self, key, msg):
        self.errors[str(key)] = msg

    ############################################################################
    # Value converters
    ############################################################################

    def _get_date_value(self, date_string):
        """Converts ISO 8601 date strings to Python datetime.date objects."""
        if date_string is None:
            # None can be used on date comparisons so assume this is what was
            # intended
            return date_string
        date = utils.date_string2date(date_string)
        if date is None:
            self._add_to_errors(
                'date %s' % str(date_string),
                'Date search parameters must be valid ISO 8601 date strings.')
        return date

    def _get_datetime_value(self, datetime_string):
        """Converts ISO 8601 datetime strings to Python datetime.datetime objects."""
        if datetime_string is None:
            # None can be used on datetime comparisons so assume this is what
            # was intended
            return datetime_string
        datetime_ = utils.datetime_string2datetime(datetime_string)
        if datetime_ is None:
            self._add_to_errors(
                'datetime %s' % str(datetime_string),
                'Datetime search parameters must be valid ISO 8601 datetime'
                ' strings.')
        return datetime_

    ############################################################################
    # Data structures
    ############################################################################
    # Alter the relations, schema and models2joins dicts in order to
    # change what types of input the query builder accepts.

    # The default set of available relations.  Relations with aliases are
    # treated as their aliases.  E.g., a search like ['Package', 'source_id' '=', ...]
    # will generate the Q expression Q(source_id=...)
    relations = {
        'exact': {},
        '=': {'alias': 'exact'},
        'ne': {},
        '!=': {'alias': 'ne'},
        'contains': {},
        'like': {'alias': 'contains'},
        'regex': {},
        'regexp': {'alias': 'regex'},
        'lt': {},
        '<': {'alias': 'lt'},
        'gt': {},
        '>': {'alias': 'gt'},
        'lte': {},
        '<=': {'alias': 'lte'},
        'gte': {},
        '>=': {'alias': 'gte'},
        'in': {}
    }

    foreign_model_relations = {
        'isnull': {},
        '=': {'alias': 'isnull'},
        '!=': {'alias': 'isnull'}
    }

    order_by_directions = {
        '': {},
        '-': {},
        'desc': {'alias': '-'},
        'descending': {'alias': '-'},
        'asc': {'alias': ''},
        'ascending': {'alias': ''}
    }

    # The schema attribute describes the database structure in a way that allows
    # the query builder to properly interpret the list-based queries and
    # generate errors where necessary. Maps model names to attribute names.
    # Attribute names whose values contain an 'alias' key are treated as the
    # value of that key, e.g., ['Package', 'enterer' ...] will be treated as
    # Package.enterer_id... The relations listed in self.relations above are the
    # default for all attributes. This can be overridden by specifying a
    # 'relation' key (cf. schema['Package']['translations'] below). Certain
    # attributes require value converters -- functions that change the value in
    # some attribute-specific way, e.g., conversion of ISO 8601 datetimes to
    # Python datetime objects.
    schema = {
        'Package': {
            'uuid': {},
            'description': {},
            'origin_pipeline': {'foreign_model': 'Pipeline', 'type': 'scalar'},
            'current_location': {'foreign_model': 'Location', 'type': 'scalar'},
            'current_path': {},
            'pointer_file_location': {'foreign_model': 'Location', 'type': 'scalar'},
            'pointer_file_path': {},
            'size': {},
            'encryption_key_fingerprint': {},
            'replicated_package': {'foreign_model': 'Package', 'type': 'scalar'},
            'package_type': {},
            'replicas': {'foreign_model': 'Package', 'type': 'collection'},
            'related_packages': {'foreign_model': 'Package', 'type': 'collection'},
            'status': {},
            'misc_attributes': {}
        },

        'Location': {
            'uuid': {},
            'space': {'foreign_model': 'Space', 'type': 'scalar'},
            'purpose': {},
            'pipeline': {'foreign_model': 'Pipeline', 'type': 'collection'},
            'relative_path': {},
            'description': {},
            'quota': {},
            'used': {},
            'enabled': {},
            'replicators': {'foreign_model': 'Location', 'type': 'collection'},
            'masters': {'foreign_model': 'Location', 'type': 'collection'}
        },

        'Space': {
            'uuid': {},
            'access_protocol': {},
            'size': {},
            'used': {},
            'path': {},
            'staging_path': {},
            'verified': {},
            'last_verified': {'value_converter': '_get_datetime_value'}
        },

        'Pipeline': {
            'uuid': {},
            'description': {},
            'remote_name': {},
            'api_username': {},
            'api_key': {},
            'enabled': {},
            'location_set': {'foreign_model': 'Location', 'type': 'collection'}
        }
    }

    model_aliases = {}

    # Maps model names to the names of other models they can be joined to for
    # queries.  The values of the join models are the attributes of the original
    # model that the joins are actually made on, e.g., outerjoin(model.Package.tags)
    models2joins = {
        'Package': {
            'File': 'files',
            'Translation': 'translations',
            'Tag': 'tags',
            'Collection': 'collections',
            'Memorizer': 'memorizers'
        },
        'File': {
            'Tag': 'tags',
            'Package': 'forms',
            'Collection': 'collections'
        },
        'Collection': {
            'Package': 'forms',
            'File': 'files',
            'Tag': 'tags'
        }
    }

    ############################################################################
    # Model getters
    ############################################################################

    def _get_model_name(self, model_name):
        """Always return model_name; store an error if model_name is invalid."""
        if model_name not in self.schema:
            self._add_to_errors(
                model_name,
                'Searching on the %s model is not permitted' % model_name)
        return model_name

    def _get_attribute_model_name(self, attribute_name, model_name):
        """Returns the name of the model X that stores the data for the attribute
        A of model M, e.g., the attribute_model_name for model_name='Package' and
        attribute_name='origin_pipeline' is 'Pipeline'.
        """
        attribute_dict = self._get_attribute_dict(attribute_name, model_name)
        try:
            return attribute_dict['foreign_model']
        except KeyError:
            self._add_to_errors(
                '%s.%s' % (model_name, attribute_name),
                'The %s attribute of the %s model does not represent a'
                ' many-to-one relation.' % (
                    attribute_name, model_name))

    ############################################################################
    # Attribute getters
    ############################################################################

    def _get_attribute_name(self, attribute_name, model_name):
        """Return attribute_name or cache an error if attribute_name is not in
        self.schema[model_name].
        """
        self._get_attribute_dict(attribute_name, model_name, report_error=True)
        return attribute_name

    def _get_attribute_dict(self, attribute_name, model_name, report_error=False):
        """Return the dict needed to validate a given attribute of a given model,
        or return None. Propagate an error (optionally) if the attribute_name is
        invalid.
        """
        attribute_dict = self.schema.get(model_name, {}).get(
            attribute_name, None)
        if attribute_dict is None and report_error:
            self._add_to_errors(
                '%s.%s' % (model_name, attribute_name),
                'Searching on %s.%s is not permitted' % (
                    model_name, attribute_name))
        return attribute_dict

    ############################################################################
    # Relation getters
    ############################################################################

    def _get_relation_name(self, relation_name, model_name, attribute_name):
        """Return relation_name or its alias; propagate an error if
        relation_name is invalid.
        """
        relation_dict = self._get_relation_dict(
            relation_name, model_name, attribute_name, report_error=True)
        try:
            return relation_dict.get('alias', relation_name)
        except AttributeError:  # relation_dict can be None
            return None

    def _get_relation_dict(self, relation_name, model_name, attribute_name,
                           report_error=False):
        attribute_relations = self._get_attribute_relations(
            attribute_name, model_name)
        try:
            relation_dict = attribute_relations.get(relation_name, None)
        except AttributeError:
            relation_dict = None
        if relation_dict is None and report_error:
            self._add_to_errors(
                '%s.%s.%s' % (model_name, attribute_name, relation_name),
                'The relation %s is not permitted for %s.%s' % (
                    relation_name, model_name, attribute_name))
        return relation_dict

    def _get_attribute_relations(self, attribute_name, model_name):
        """Return the data structure encoding what relations are valid for the
        input attribute name.
        """
        attribute_dict = self._get_attribute_dict(attribute_name, model_name)
        try:
            if attribute_dict.get('foreign_model'):
                return self.foreign_model_relations
            return self.relations
        except AttributeError:  # attribute_dict can be None
            return None

    ############################################################################
    # Value getters
    ############################################################################

    @staticmethod
    def _normalize(value):
        def normalize_if_string(value):
            if isinstance(value, str):
                return utils.normalize(value)
            return value
        value = normalize_if_string(value)
        if isinstance(value, list):
            value = [normalize_if_string(i) for i in value]
        return value

    def _get_value_converter(self, attribute_name, model_name):
        attribute_dict = self._get_attribute_dict(attribute_name, model_name)
        try:
            value_converter_name = attribute_dict.get('value_converter', '')
            return getattr(self, value_converter_name, None)
        except AttributeError:  # attribute_dict can be None
            return None

    def _get_value(self, value, model_name, attribute_name, relation_name=None):
        """Unicode normalize & modify the value using a value_converter (if
        necessary).
        """
        # unicode normalize (NFD) search patterns; we might want to parameterize this
        value = self._normalize(value)
        value_converter = self._get_value_converter(attribute_name, model_name)
        if value_converter is not None:
            if isinstance(value, list):
                value = [value_converter(li) for li in value]
            else:
                value = value_converter(value)
        attribute_dict = self._get_attribute_dict(attribute_name, model_name)
        if (attribute_dict.get('foreign_model') and
                relation_name and value is None):
            if relation_name == '=':
                value = True
            if relation_name == '!=':
                value = False
        return value

    ############################################################################
    # Filter expression getters
    ############################################################################

    @staticmethod
    def _get_invalid_filter_expression_message(
            model_name, attribute_name, relation_name, value):
        return 'Invalid filter expression: %s.%s.%s(%s)' % (
            model_name, attribute_name, relation_name, repr(value))

    def _get_invalid_model_attribute_errors(self, *args):
        """Avoid catching a (costly) RuntimeError by preventing _get_Q_expression
        from attempting to build relation(value) or attribute.has(relation(value)).
        We do this by returning a non-empty list of error tuples if Model.attribute
        errors are present in self.errors.
        """
        try:
            (value, model_name, attribute_name, relation_name,
             attribute_model_name, attribute_model_attribute_name) = args
        except ValueError:
            raise TypeError(
                '_get_invalid_model_attribute_errors() missing 6 required'
                ' positional arguments: \'value\', \'model_name\','
                ' \'attribute_name\', \'relation_name\','
                ' \'attribute_model_name\', and'
                ' \'attribute_model_attribute_name\'')
        e = []
        if attribute_model_name:
            error_key = '%s.%s' % (
                attribute_model_name, attribute_model_attribute_name)
            if (self.errors.get(error_key) ==
                    'Searching on the %s is not permitted' % error_key):
                e.append(
                    ('%s.%s.%s' % (attribute_model_name,
                                   attribute_model_attribute_name,
                                   relation_name),
                     self._get_invalid_filter_expression_message(
                         attribute_model_name,
                         attribute_model_attribute_name,
                         relation_name,
                         value)))
        error_key = '%s.%s' % (model_name, attribute_name)
        if (self.errors.get(error_key) ==
                'Searching on %s is not permitted' % error_key):
            e.append(('%s.%s.%s' % (model_name, attribute_name, relation_name),
                      self._get_invalid_filter_expression_message(
                          model_name, attribute_name, relation_name, value)))
        return e

    def _get_meta_relation(self, attribute, model_name, attribute_name):
        """Return the has() or the any() method of the input attribute, depending
        on the value of schema[model_name][attribute_name]['type'].
        """
        return getattr(attribute, {'scalar': 'has', 'collection': 'any'}[
            self.schema[model_name][attribute_name]['type']])

    @staticmethod
    def _get_Q_expression(attribute_name, relation_name, value):
        return Q(**{'{}__{}'.format(attribute_name, relation_name): value})

    def _get_simple_Q_expression(self, *args):
        """Build a Q expression. Examples::

            >>> ['Package', 'description', '=', 'abc']
            >>> Q(description__exact='abc')

            >>> ['Package', 'description', '!=', 'abc']
            >>> Q(description__ne='abc')

            >>> ['Package', 'origin_pipeline', 'description', 'like', 'J%']
            >>> Q(origin_pipeline__description__contains='J%')

            >>> ['Package', 'replicas', 'uuid', 'contains', 'a%']
            >>> Q(replicas__uuid__contains='a%')
        """
        model_name = self._get_model_name(args[0])
        attribute_name = self._get_attribute_name(args[1], model_name)
        if len(args) == 4:
            relation_name = self._get_relation_name(
                args[2], model_name, attribute_name)
            value = self._get_value(
                args[3], model_name, attribute_name, relation_name=args[2])
            return self._get_Q_expression(
                attribute_name, relation_name, value)
        attribute_model_name = self._get_attribute_model_name(
            attribute_name, model_name)
        attribute_model_attribute_name = self._get_attribute_name(
            args[2], attribute_model_name)
        relation_name = self._get_relation_name(
            args[3], attribute_model_name, attribute_model_attribute_name)
        value = self._get_value(
            args[4], attribute_model_name, attribute_model_attribute_name,
            relation_name=args[3])
        attribute_name = '{}__{}'.format(
            attribute_name, attribute_model_attribute_name)
        return self._get_Q_expression(
            attribute_name, relation_name, value)

    def get_search_parameters(self):
        """Given the view's resource-configured QueryBuilder instance,
        return the list of attributes and their aliases and licit relations
        relevant to searching.
        """
        return {
            'attributes':
                self.schema[self.model_name],
            'relations': self.relations
        }
