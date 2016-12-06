# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
import logging
from django.utils.translation import ugettext_lazy as _
from django.forms import models
from django import forms
from logical.widgets.database_offering_field import DatabaseOfferingWidget
from dbaas_cloudstack.models import CloudStackPack
from drivers.factory import DriverFactory
from backup.models import Snapshot
from physical.models import Plan, Environment, Engine, DiskOffering
from logical.forms.fields import AdvancedModelChoiceField
from logical.models import Database
from logical.validators import database_name_evironment_constraint

LOG = logging.getLogger(__name__)


class CloneDatabaseForm(forms.Form):
    database_clone = forms.CharField(
        label=u'Destination database', max_length=64, required=True)
    environment = forms.ModelChoiceField(queryset=Environment.objects, widget=forms.Select(
        attrs={'class': 'environment'}), required='True',)
    engine = forms.CharField(widget=forms.HiddenInput(),)
    origin_database_id = forms.CharField(widget=forms.HiddenInput())
    old_plan = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):

        super(CloneDatabaseForm, self).__init__(*args, **kwargs)

        if 'initial' in kwargs:
            instance = Database.objects.get(
                id=kwargs['initial']['origin_database_id'])
        elif 'origin_database_id' in self.data:
            instance = Database.objects.get(id=self.data['origin_database_id'])

        if instance:
            LOG.debug("instance database form found! %s" % instance)
            self.define_engine_field(database=instance)
            self.define_available_plans(database=instance)

        self.initial['old_plan'] = instance.plan.id

    def define_engine_field(self, database):
        self.initial['engine'] = database.infra.engine.id

    def define_available_plans(self, database):
        self.fields['plan'] = forms.ModelChoiceField(queryset=Plan.objects.filter(engine=database.infra.engine,
                                                                                  is_active=True),
                                                     widget=forms.Select(
                                                         attrs={'class': 'plan'}),
                                                     required=True,)

    def clean(self):
        cleaned_data = super(CloneDatabaseForm, self).clean()
        if 'database_clone' in cleaned_data:

            origindatabase = Database.objects.get(
                pk=cleaned_data['origin_database_id'])

            # for infra in DatabaseInfra.objects.filter(environment=origindatabase.environment,plan=origindatabase.plan):
            #    if infra.databases.filter(name=cleaned_data['database_clone']):
            #        self._errors["database_clone"] = self.error_class([_("this name already exists in the selected environment")])

            if len(cleaned_data['database_clone']) > 40:
                self._errors["database_clone"] = self.error_class(
                    [_("Database name too long")])

            dbs = origindatabase.team.databases_in_use_for(
                origindatabase.environment)

            database_alocation_limit = origindatabase.team.database_alocation_limit
            LOG.debug("dbs: %s | type: %s" % (dbs, type(dbs)))
            if (database_alocation_limit != 0 and len(dbs) >= database_alocation_limit):
                LOG.warning("The database alocation limit of %s has been exceeded for the team: %s => %s" % (
                    database_alocation_limit, origindatabase.team, list(dbs)))
                raise forms.ValidationError([_("The database alocation limit of %s has been exceeded for the team:  %s => %s") % (
                    database_alocation_limit, origindatabase.team, list(dbs))])

            driver = DriverFactory.get_driver_class(
                origindatabase.plan.engines[0].name)
            if cleaned_data['database_clone'] in driver.RESERVED_DATABASES_NAME:
                raise forms.ValidationError(
                    _("%s is a reserved database name" % cleaned_data['database_clone']))

            environment = cleaned_data.get('environment', None)
            database_name = cleaned_data.get('database_clone', None)

            if Database.objects.filter(name=database_name,
                                       environment=environment):
                raise forms.ValidationError(
                    _("There is already a database called {} on {}".format(database_name,
                                                                           environment)))

        if database_name_evironment_constraint(database_name, environment.name):
            raise forms.ValidationError(
                _('%s already exists in production!') % database_name
            )

            if self._errors:
                return cleaned_data

        return cleaned_data

class DatabaseForm(models.ModelForm):
    plan = AdvancedModelChoiceField(queryset=Plan.objects.filter(is_active='True'),
                                    required=False, widget=forms.RadioSelect,
                                    empty_label=None)
    engine = forms.ModelChoiceField(queryset=Engine.objects)
    environment = forms.ModelChoiceField(queryset=Environment.objects)

    class Meta:
        model = Database
        fields = (
            'name', 'description', 'subscribe_to_email_events',
            'project', 'team', 'is_in_quarantine',
        )

    def remove_fields_not_in_models(self):
        fields_to_remove = ["plan", "engine", "environment"]
        for field_name in fields_to_remove:
            if field_name in self.fields:
                del self.fields[field_name]

    @classmethod
    def setup_offering_field(cls, form, db_instance):
        widget = DatabaseOfferingWidget(
            id='resizeDatabase',
            url=db_instance.get_resize_url(),
            label='Resize VM',
            attrs={
                'readonly': 'readonly',
                'database': db_instance
            }
        )

        form.declared_fields['offering'] = forms.CharField(
            widget=widget, required=False, initial=db_instance.offering
        )

    @classmethod
    def setup_disk_offering_field(cls, form, db_instance):
        widget = DatabaseOfferingWidget(
            id='resizeDisk',
            url=db_instance.get_disk_resize_url(),
            label='Resize Disk',
            attrs={
                'readonly':'readonly',
                'database': db_instance
            }
        )

        form.declared_fields['disk_offering'] = forms.CharField(
            widget=widget, required=False,
            initial=db_instance.databaseinfra.disk_offering
        )

    def __init__(self, *args, **kwargs):

        super(DatabaseForm, self).__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        if instance:
            LOG.debug("instance database form found! %s" % instance)
            # remove fields not in models
            self.remove_fields_not_in_models()

        else:
            self.fields['is_in_quarantine'].widget = forms.HiddenInput()

    def _validate_description(self, cleaned_data):
        if 'description' in cleaned_data:
            if not cleaned_data.get('description', None):
                self._errors["description"] = self.error_class(
                    [_("Description: This field is required.")])

    def _validate_project(self, cleaned_data):
        if 'project' in cleaned_data:
            if not cleaned_data.get('project', None):
                self._errors["project"] = self.error_class(
                    [_("Project: This field is required.")])

    def _validate_team(self, cleaned_data):
        if 'team' in cleaned_data:
            if not cleaned_data['team']:
                LOG.warning("No team specified in database form")
                self._errors["team"] = self.error_class(
                    [_("Team: This field is required.")])

    def _validate_team_resources(self, cleaned_data):
        team = cleaned_data['team']

        if team:
            dbs = team.databases_in_use_for(cleaned_data['environment'])
            database_alocation_limit = team.database_alocation_limit
            LOG.debug("dbs: %s | type: %s" % (dbs, type(dbs)))

            if (database_alocation_limit != 0 and len(dbs) >= database_alocation_limit):
                LOG.warning("The database alocation limit of %s has been exceeded for the selected team %s => %s" % (
                    database_alocation_limit, team, list(dbs)))
                self._errors["team"] = self.error_class(
                    [_("The database alocation limit of %s has been exceeded for the selected team: %s") % (database_alocation_limit, list(dbs))])

    def _validate_name(self, cleaned_data):
        if len(cleaned_data['name']) > 40:
            self._errors["name"] = self.error_class(
                [_("Database name too long")])

        plan = cleaned_data['plan']
        driver = DriverFactory.get_driver_class(plan.engines[0].name)
        if cleaned_data['name'] in driver.RESERVED_DATABASES_NAME:
            raise forms.ValidationError(
                _("%s is a reserved database name" % cleaned_data['name']))

    def clean(self):
        cleaned_data = super(DatabaseForm, self).clean()

        # if there is an instance, that means that we are in a edit page and therefore
        # it should return the default cleaned_data
        if self.instance and self.instance.id:
            self._validate_project(cleaned_data)
            self._validate_description(cleaned_data)
            self._validate_team(cleaned_data)
            return cleaned_data

        if not self.is_valid():
            raise forms.ValidationError(self.errors)

        if 'plan' in cleaned_data:
            plan = cleaned_data.get('plan', None)
            if not plan:
                self._errors["plan"] = self.error_class(
                    [_("Plan: This field is required.")])

        self._validate_name(cleaned_data)
        self._validate_project(cleaned_data)
        self._validate_description(cleaned_data)
        self._validate_team(cleaned_data)

        if 'environment' in cleaned_data:
            environment = cleaned_data.get('environment', None)
            database_name = cleaned_data.get('name', None)
            if not environment or environment not in plan.environments.all():
                raise forms.ValidationError(
                    _("Invalid plan for selected environment."))

            if Database.objects.filter(name=database_name,
                                       environment=environment):
                self._errors["name"] = self.error_class(
                    [_("this name already exists in the selected environment")])
                del cleaned_data["name"]

        self._validate_team_resources(cleaned_data)
        if database_name_evironment_constraint(database_name, environment.name):
            raise forms.ValidationError(
                _('%s already exists in production!') % database_name
            )

        if self._errors:
            return cleaned_data

        return cleaned_data

    def save_m2m(self, *args, **kwargs):
        pass


class ResizeDatabaseForm(forms.Form):
    database_id = forms.CharField(widget=forms.HiddenInput())
    original_offering_id = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):

        super(ResizeDatabaseForm, self).__init__(*args, **kwargs)

        if 'initial' in kwargs:
            instance = Database.objects.get(
                id=kwargs['initial']['database_id'])

            if instance:
                LOG.debug("instance database form found! %s" % instance)
                if instance.plan.provider == instance.plan.CLOUDSTACK:
                    LOG.debug("Cloudstack plan found!")
                    self.define_target_offering_field(
                        database_instance=instance, origin_offer=kwargs['initial']['original_offering_id'])

    def define_target_offering_field(self, database_instance, origin_offer):
        self.fields['target_offer'] = forms.ModelChoiceField(
            queryset=CloudStackPack.objects.filter(
                offering__region__environment=database_instance.environment,
                engine_type__name=database_instance.engine_type
            ).exclude(offering__serviceofferingid=origin_offer),
            label=u'New Offering',
            required=True)

    def clean(self):
        cleaned_data = super(ResizeDatabaseForm, self).clean()

        if 'target_offer' in cleaned_data:

            if cleaned_data['target_offer'].offering.serviceofferingid == cleaned_data['original_offering_id']:
                raise forms.ValidationError(
                    _("new offering must be different from the current"))

            if self._errors:
                return cleaned_data

        return cleaned_data


class RestoreDatabaseForm(forms.Form):
    database_id = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super(RestoreDatabaseForm, self).__init__(*args, **kwargs)

        if 'initial' in kwargs:
            instance = Database.objects.get(
                id=kwargs['initial']['database_id'])

            if instance:
                LOG.debug("instance database form found! %s" % instance)
                if instance.plan.provider == instance.plan.CLOUDSTACK:
                    LOG.debug("Cloudstack plan found!")
                    self.define_target_snapshot_field(
                        database_instance=instance)

    def define_target_snapshot_field(self, database_instance):
        instances = database_instance.databaseinfra.instances.all()
        self.fields['target_snapshot'] = forms.ModelChoiceField(
            queryset=Snapshot.objects.filter(instance__in=instances,
                                             purge_at=None),
            label=u'Recover to point',
            required=True)

    def clean(self):
        cleaned_data = super(RestoreDatabaseForm, self).clean()

        if 'target_snapshot' not in cleaned_data:
            LOG.info('FOX001')
            raise forms.ValidationError(
                _("Please select one snapshot"))

        return cleaned_data


class LogDatabaseForm(forms.Form):
    database_id = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):

        super(LogDatabaseForm, self).__init__(*args, **kwargs)
        if 'initial' in kwargs:
            instance = Database.objects.get(
                id=kwargs['initial']['database_id'])

            if instance:
                LOG.debug("instance database form found! %s" % instance)


class DiskResizeDatabaseForm(forms.Form):

    def __init__(self, database, data=None):
        super(DiskResizeDatabaseForm, self).__init__(data=data)

        self.database = database
        self.disk_offering = database.databaseinfra.disk_offering

        disk_used_size_kb = self.database.databaseinfra.disk_used_size_in_kb
        if not disk_used_size_kb:
            disk_used_size_kb = self.database.used_size_in_kb

        self.fields['target_offer'] = forms.ModelChoiceField(
            queryset=DiskOffering.objects.filter(
                available_size_kb__gt=disk_used_size_kb
            ).exclude(
                id=database.databaseinfra.disk_offering.id
            ),
            label=u'New Disk',
            required=True
        )

    def clean(self):
        cleaned_data = super(DiskResizeDatabaseForm, self).clean()

        if 'target_offer' in cleaned_data:
            if cleaned_data['target_offer'] == self.disk_offering:
                raise forms.ValidationError(
                    _("New offering must be different from the current")
                )

            current_database_size = round(self.database.used_size_in_gb, 2)
            new_disk_size = round(
                cleaned_data['target_offer'].available_size_gb(), 2
            )
            if current_database_size >= new_disk_size:
                raise forms.ValidationError(
                    _("Your database has {} GB, please choose "
                      "a bigger disk".format(current_database_size)
                      )
                )

        return cleaned_data
