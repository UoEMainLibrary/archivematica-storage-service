from __future__ import absolute_import
import logging
import re

from formencode.compound import Any
from formencode.foreach import ForEach
from formencode.schema import Schema
from formencode.validators import (
    DateConverter,
    FancyValidator,
    Int,
    Invalid,
    IPAddress,
    OneOf,
    StringBoolean,
    UnicodeString,
    URL,
)

from locations import models


LOGGER = logging.getLogger(__name__)


class ValidModelObject(FancyValidator):
    """Validator for input values that are primary keys of model objects. Value
    must be the pk of an existing model of the type specified in the
    ``model_name`` kwarg. If valid, the model object is returned. Example
    usage: ValidModelObject(model_name='Package').
    """

    messages = {
        'invalid_model': 'There is no %(model_name_eng)s with pk %(pk)s.'
    }

    def _convert_to_python(self, value, state):
        if value in ['', None]:
            return None
        else:
            pk = Int().to_python(value, state)
            pk = getattr(self, 'pk', 'uuid')
            model_object = getattr(models, self.model_name).objects.get(
                **{pk: value})
            if model_object is None:
                model_name_eng=camel_case2lower_space(self.model_name)
                raise Invalid(
                    self.message('invalid_model', state, id=pk,
                                 model_name_eng=model_name_eng),
                    value, state)
            else:
                return model_object


class PackageSchema(Schema):
    allow_extra_fields = True
    filter_extra_fields = True
    current_location = ValidModelObject(model_name='Location')
    current_path = UnicodeString()
    description = UnicodeString(max=256)
    encryption_key_fingerprint = UnicodeString(max=512)
    misc_attributes = UnicodeString()
    origin_pipeline = ValidModelObject(model_name='Pipeline')
    package_type = OneOf(models.Package.PACKAGE_TYPE_CHOICES)
    pointer_file_location = ValidModelObject(model_name='Location')
    pointer_file_path = UnicodeString()
    related_packages = ForEach(ValidModelObject(model_name='Package'))
    replicated_package = ValidModelObject(model_name='Package')
    size = Int(min=0)
    status = OneOf(models.Package.STATUS_CHOICES)


class PipelineSchema(Schema):
    allow_extra_fields = True
    filter_extra_fields = True
    active = StringBoolean()
    api_key = UnicodeString(max=256)
    api_username = UnicodeString(max=256)
    description = UnicodeString(max=256)
    enabled = StringBoolean()
    remote_name = Any(validators=[IPAddress(), URL()])


class SpaceSchema(Schema):
    allow_extra_fields = True
    filter_extra_fields = True
    access_protocol = OneOf(models.Space.ACCESS_PROTOCOL_CHOICES)
    last_verified = DateConverter(month_style='mm/dd/yyyy')
    path = UnicodeString(max=256)
    size = Int(min=0)
    staging_path = UnicodeString(max=256)
    used = Int(min=0)
    verified = StringBoolean()


class LocationSchema(Schema):
    allow_extra_fields = True
    filter_extra_fields = True
    description = UnicodeString(max=256)
    purpose = OneOf(models.Location.PURPOSE_CHOICES)
    relative_path = UnicodeString()
    quota = Int(min=0)
    used = Int(min=0)
    enabled = StringBoolean()
    space = ValidModelObject(model_name='Pipeline')
    pipeline = ValidModelObject(model_name='Space')
    replicators = ForEach(ValidModelObject(model_name='Location'))


def camel_case2lower_space(name):
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1 \2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1 \2', s1).lower()
