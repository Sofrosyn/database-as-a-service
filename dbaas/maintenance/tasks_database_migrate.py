from maintenance.models import (
    DatabaseMigrate,
    HostMigrate,
    DatabaseMigrateStandAlonePhase1,
    DatabaseMigrateStandAlonePhase2
)
from util.providers import (
    get_database_migrate_steps,
    get_database_migrate_stand_alone_phase1_steps
)
from workflow.workflow import steps_for_instances, rollback_for_instances_full
from copy import copy
from datetime import datetime
from physical.models import Offering


def get_steps(database):
    class_path = database.infra.plan.replication_topology.class_path
    return get_database_migrate_steps(class_path)


def build_migrate_hosts(hosts_zones, migrate, step_manager=None):
    instances = []
    for host, zone in hosts_zones.iteritems():
        instance = host.instances.first()
        if step_manager:
            host_migrate = step_manager.hosts.get(host=instance.hostname)
            host_migrate.id = None
            host_migrate.finished_at = None
            host_migrate.created_at = datetime.now()
        else:
            host_migrate = HostMigrate()
        host_migrate.task = migrate.task
        host_migrate.host = instance.hostname
        host_migrate.zone = zone
        host_migrate.environment = migrate.environment
        host_migrate.database_migrate = migrate
        host_migrate.save()
        instances.append(instance)
    return instances


def database_environment_migrate(
    database, new_environment, new_offering, task, hosts_zones, since_step=None,
    step_manager=None
):
    if step_manager:
        database_migrate = copy(step_manager)
        database_migrate.id = None
        database_migrate.finished_at = None
        database_migrate.created_at = datetime.now()
    else:
        database_migrate = DatabaseMigrate()
    database_migrate.task = task
    database_migrate.database = database
    database_migrate.environment = new_environment
    database_migrate.origin_environment = database.environment
    database_migrate.offering = new_offering
    database_migrate.origin_offering = database.infra.offering
    database_migrate.save()

    instances = build_migrate_hosts(hosts_zones, database_migrate, step_manager=step_manager)
    instances = sorted(instances, key=lambda k: k.dns)
    steps = get_steps(database)
    result = steps_for_instances(
        steps, instances, task, database_migrate.update_step, since_step,
        step_manager=step_manager
    )
    database_migrate = DatabaseMigrate.objects.get(id=database_migrate.id)
    if result:
        database = database_migrate.database
        database.environment = database_migrate.environment
        database.save()
        infra = database.infra
        infra.environment = database_migrate.environment
        infra.plan = infra.plan.get_equivalent_plan_for_env(
            database_migrate.environment
        )
        infra.save()
        database_migrate.set_success()
        task.set_status_success('Database migrated with success')
    else:
        database_migrate.set_error()
        task.set_status_error('Could not migrate database')


def rollback_database_environment_migrate(migrate, task):
    hosts_zones = migrate.hosts_zones
    migrate.id = None
    migrate.created_at = None
    migrate.finished_at = None
    migrate.task = task
    migrate.save()

    instances = build_migrate_hosts(hosts_zones, migrate)
    instances = sorted(instances, key=lambda k: k.dns)
    steps = get_steps(migrate.database)
    result = rollback_for_instances_full(
        steps, instances, task, migrate.get_current_step, migrate.update_step
    )
    migrate = DatabaseMigrate.objects.get(id=migrate.id)
    if result:
        migrate.set_rollback()
        task.set_status_success('Rollback executed with success')
    else:
        migrate.set_error()
        task.set_status_error(
            'Could not do rollback\n'
            'Please check error message and do retry'
        )


def database_environment_migrate_stand_alone_phase1(
    database, new_environment, 
    task, since_step=None, step_manager=None
):
    if step_manager:
        database_migrate = copy(step_manager)
        database_migrate.id = None
        database_migrate.finished_at = None
        database_migrate.created_at = datetime.now()
    else:
        database_migrate = DatabaseMigrateStandAlonePhase1()
    database_migrate.task = task
    database_migrate.database = database
    database_migrate.source_environment = database.environment
    database_migrate.target_environment = new_environment
    database_migrate.source_plan = database.plan
    database_migrate.target_plan = database.plan ## tmp code
    database_migrate.source_offering = database.infra.offering
    database_migrate.target_offering = Offering.get_equivalent_offering(
                                        database, new_environment)
    database_migrate.save()

    instances = database.infra.instances.all() ## tmp code
    class_path = database.infra.plan.replication_topology.class_path
    steps = get_database_migrate_stand_alone_phase1_steps(class_path)
    result = steps_for_instances(
        steps, instances, task, database_migrate.update_step, since_step,
        step_manager=step_manager
    )
    if result:
        database_migrate.set_success()
        task.set_status_success('Database migrated with success')
    else:
        database_migrate.set_error()
        task.set_status_error('Could not migrate database')


def rollback_database_environment_migrate_stand_alone_phase1(migrate, task):
    migrate.id = None
    migrate.created_at = None
    migrate.finished_at = None
    migrate.task = task
    migrate.save()

    database = migrate.database
    instances = database.infra.instances.all() ## tmp code
    class_path = database.infra.plan.replication_topology.class_path
    steps = get_database_migrate_stand_alone_phase1_steps(class_path)

    result = rollback_for_instances_full(
        steps, instances, task, migrate.get_current_step, migrate.update_step
    )
    migrate = DatabaseMigrate.objects.get(id=migrate.id)
    if result:
        migrate.set_rollback()
        task.set_status_success('Rollback executed with success')
    else:
        migrate.set_error()
        task.set_status_error(
            'Could not do rollback\n'
            'Please check error message and do retry'
        )
