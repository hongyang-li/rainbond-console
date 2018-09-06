# -*- coding: utf8 -*-
"""
  Created on 2018/5/25.
  应用迁移
"""
import json
import logging
from django.db import transaction

from console.repositories.backup_repo import backup_record_repo
from console.repositories.group import group_repo
from console.apiclient.regionapi import RegionInvokeApi
from console.utils.crypt import make_uuid
from console.repositories.migration_repo import migrate_repo
from console.models.services import TenantServiceInfo, TenantServiceEnvVar, TenantServiceVolume, TenantServicesPort, \
    TenantServiceEnv, ServiceDomain, ServiceEvent, ServiceProbe, TenantServiceAuth, ImageServiceRelation, \
    TenantServiceRelation, \
    TenantServiceMountRelation
from console.models.services import ServiceRelPerms, ServiceSourceInfo
from console.repositories.team_repo import team_repo
from console.models.labels import ServiceLabels
from console.services.group_service import group_service
from console.constants import AppMigrateType

region_api = RegionInvokeApi()
logger = logging.getLogger("default")


class GroupappsMigrateService(object):

    def __get_restore_type(self, current_tenant, current_region, migrate_team, migrate_region):
        """获取恢复的类型"""
        if current_region != migrate_region:
            return AppMigrateType.OTHER_REGION
        if migrate_team.tenant_name != current_tenant.tenant_name:
            return AppMigrateType.CURRENT_REGION_OTHER_TENANT
        return AppMigrateType.CURRENT_REGION_CURRENT_TENANT

    def __copy_backup_record(self, restore_mode, origin_backup_record, current_team, current_region, migrate_team,
                             migrate_region, migrate_type):
        """拷贝备份数据"""
        services = group_service.get_group_services(origin_backup_record.group_id)
        if not services and migrate_type == "recover":
            new_group = group_repo.get_group_by_id(origin_backup_record.group_id)
        else:
            new_group = self.__create_new_group(migrate_team.tenant_id, migrate_region, origin_backup_record.group_id)
        if restore_mode != AppMigrateType.CURRENT_REGION_CURRENT_TENANT:
            # 获取原有数据中心数据
            original_data = region_api.get_backup_status_by_backup_id(current_region, current_team.tenant_name,
                                                                      origin_backup_record.backup_id)

            new_event_id = make_uuid()
            new_group_uuid = make_uuid()
            new_data = original_data["bean"]
            new_data["event_id"] = new_event_id
            new_data["group_id"] = new_group_uuid
            # 存入其他数据中心
            body = region_api.copy_backup_data(migrate_region, migrate_team.tenant_name, new_data)
            bean = body["bean"]
            params = origin_backup_record.to_dict()
            params.pop("ID")
            params["team_id"] = migrate_team.tenant_id
            params["event_id"] = new_event_id
            params["group_id"] = new_group.ID
            params["group_uuid"] = new_group_uuid
            params["region"] = migrate_region
            params["backup_id"] = bean["backup_id"]

            new_backup_record = backup_record_repo.create_backup_records(**params)
            return new_group, new_backup_record
        return new_group, None

    def __create_new_group(self, tenant_id, region, old_group_id):

        old_group = group_repo.get_group_by_id(old_group_id)
        new_group_name = '_'.join([old_group.group_name, make_uuid()[-4:]])

        new_group = group_repo.add_group(tenant_id, region, new_group_name)
        return new_group

    def start_migrate(self, user, current_team, current_region, migrate_team, migrate_region, backup_id, migrate_type):

        backup_record = backup_record_repo.get_record_by_backup_id(current_team.tenant_id, backup_id)
        if not backup_record:
            return 404, "无备份记录", None

        if migrate_type == "recover":
            is_all_services_closed = self.__check_group_service_status(current_region, current_team,
                                                                       backup_record.group_id)
            if not is_all_services_closed:
                return 409, "恢复备份请确保当前组下的应用全部关闭", None

        restore_mode = self.__get_restore_type(current_team, current_region, migrate_team, migrate_region)

        # 数据迁移到其他地方先处理数据中心数据拷贝
        new_group, new_backup_record = self.__copy_backup_record(restore_mode, backup_record, current_team,
                                                                 current_region, migrate_team,
                                                                 migrate_region, migrate_type)
        if not new_backup_record:
            new_backup_record = backup_record

        backup_service_info = json.loads(new_backup_record.backup_server_info)
        service_slug = backup_service_info["slug_info"]
        service_image = backup_service_info["image_info"]

        data = {
            "backup_id": new_backup_record.backup_id,
            "restore_mode": restore_mode,
            "tenant_id": migrate_team.tenant_id,
            "slug_info": service_slug,
            "image_info": service_image
        }
        body = region_api.star_apps_migrate_task(migrate_region, migrate_team.tenant_name, new_backup_record.backup_id,
                                                 data)

        # 创建迁移记录
        params = {
            "group_id": new_group.ID,
            "group_uuid": new_backup_record.group_uuid,
            "event_id": make_uuid(),
            "version": backup_record.version,
            "backup_id": new_backup_record.backup_id,
            "migrate_team": migrate_team.tenant_name,
            "migrate_region": migrate_region,
            "status": "starting",
            "user": user.nick_name,
            "restore_id": body["bean"]["restore_id"],
            "original_group_id": backup_record.group_id,
            "original_group_uuid": backup_record.group_uuid,
            "migrate_type": migrate_type

        }
        migrate_record = migrate_repo.create_migrate_record(**params)

        return 200, "操作成功，开始迁移", migrate_record

    def __check_group_service_status(self, region, tenant, group_id):
        services = group_service.get_group_services(group_id)
        service_ids = [s.service_id for s in services]
        if not service_ids:
            return True
        body = region_api.service_status(region, tenant.tenant_name,
                                         {"service_ids": service_ids, "enterprise_id": tenant.enterprise_id})
        status_list = body["list"]
        for status in status_list:
            if status["status"] not in ("closed", "undeploy"):
                return False
        return True

    def get_and_save_migrate_status(self, user, restore_id):
        migrate_record = migrate_repo.get_by_restore_id(restore_id)
        if not migrate_record:
            return 404, "无此记录", None
        if migrate_record.status == "starting":

            data = region_api.get_apps_migrate_status(migrate_record.migrate_region, migrate_record.migrate_team,
                                                      migrate_record.backup_id, restore_id)

            bean = data["bean"]
            status = bean["status"]
            service_change = bean["service_change"]
            logger.debug("service change : {0}".format(service_change))
            metadata = bean["metadata"]
            migrate_team = team_repo.get_tenant_by_tenant_name(migrate_record.migrate_team)
            if status == "success":
                with transaction.atomic():
                    try:
                        self.save_data(migrate_team, migrate_record.migrate_region, user, service_change,
                                       json.loads(metadata), migrate_record.group_id)
                    except Exception as e:
                        migrate_record.status = "failed"
                        migrate_record.save()
                        raise e
                    if migrate_record.migrate_type == "recover":
                        # 如果为恢复操作，将原有备份和迁移的记录的组信息修改
                        backup_record_repo.get_record_by_group_id(migrate_record.original_group_id).update(
                            group_id=migrate_record.group_id)
                        self.update_migrate_original_group_id(migrate_record.original_group_id, migrate_record.group_id)
            migrate_record.status = status
            migrate_record.save()

        return 200, "success", migrate_record

    def save_data(self, migrate_tenant, migrate_region, user, changed_service_map, metadata, group_id):

        group = group_repo.get_group_by_id(group_id)
        apps = metadata["apps"]

        old_new_service_id_map = dict()
        service_relations_list = []
        service_mnt_list = []
        for app in apps:
            service_base_info = app["service_base"]

            new_service_id = changed_service_map[service_base_info["service_id"]]["ServiceID"]
            new_service_alias = changed_service_map[service_base_info["service_id"]]["ServiceAlias"]

            ts = self.__init_app(app["service_base"], new_service_id, new_service_alias, user, migrate_region,
                                 migrate_tenant)
            old_new_service_id_map[app["service_base"]["service_id"]] = ts.service_id
            group_service.add_service_to_group(migrate_tenant, migrate_region, group.ID, ts.service_id)
            self.__save_env(migrate_tenant, ts, app["service_env_vars"])
            self.__save_volume(ts, app["service_volumes"])
            lb_mapping_port = changed_service_map[service_base_info["service_id"]].get("LBPorts", None)
            self.__save_port(migrate_tenant, ts, app["service_ports"], lb_mapping_port)
            self.__save_compile_env(ts, app["service_compile_env"])
            self.__save_service_label(migrate_tenant, ts, migrate_region, app["service_labels"])
            # self.__save_service_domain(ts, app["service_domains"])
            self.__save_service_event(migrate_tenant, ts, app["service_events"])
            self.__save_service_perms(ts, app["service_perms"])
            self.__save_service_probes(ts, app["service_probes"])
            self.__save_service_source(migrate_tenant, ts, app["service_source"])
            self.__save_service_auth(ts, app["service_auths"])
            self.__save_service_image_relation(migrate_tenant, ts, app["image_service_relation"])

            service_relations = app["service_relation"]
            service_mnts = app["service_mnts"]

            if service_relations:
                service_relations_list[0:0] = list(service_relations)
            if service_mnts:
                service_mnt_list[0:0] = list(service_mnts)
            # 更新状态
            ts.create_status = "complete"
            ts.save()

        self.__save_service_relations(migrate_tenant, service_relations_list, old_new_service_id_map)
        self.__save_service_mnt_relation(migrate_tenant, service_mnt_list, old_new_service_id_map)

    def __init_app(self, service_base_info, new_service_id, new_servie_alias, user, region, tenant):
        service_base_info.pop("ID")
        ts = TenantServiceInfo(**service_base_info)
        ts.service_id = new_service_id
        ts.service_alias = new_servie_alias
        ts.service_region = region
        ts.creater = user.user_id
        ts.tenant_id = tenant.tenant_id
        ts.create_status = "creating"
        ts.save()
        return ts

    def __save_env(self, tenant, service, tenant_service_env_vars):
        env_list = []
        for env in tenant_service_env_vars:
            env.pop("ID")
            new_env = TenantServiceEnvVar(**env)
            new_env.tenant_id = tenant.tenant_id
            new_env.service_id = service.service_id
            env_list.append(new_env)
        if env_list:
            TenantServiceEnvVar.objects.bulk_create(env_list)

    def __save_volume(self, service, tenant_service_volumes):
        volume_list = []
        for volume in tenant_service_volumes:
            volume.pop("ID")
            new_volume = TenantServiceVolume(**volume)
            new_volume.service_id = service.service_id
            volume_list.append(new_volume)
        if volume_list:
            TenantServiceVolume.objects.bulk_create(volume_list)

    def __save_port(self, tenant, service, tenant_service_ports, lb_port_info):
        port_list = []
        for port in tenant_service_ports:
            port.pop("ID")
            new_port = TenantServicesPort(**port)
            new_port.service_id = service.service_id
            new_port.tenant_id = tenant.tenant_id
            if lb_port_info:
                lb_port = lb_port_info.get(str(port["container_port"]), None)
                if lb_port is not None:
                    new_port.lb_mapping_port = lb_port
            port_list.append(new_port)
        if port_list:
            TenantServicesPort.objects.bulk_create(port_list)

    def __save_compile_env(self, service, compile_env):
        if compile_env:
            compile_env.pop("ID")
            new_compile_env = TenantServiceEnv(**compile_env)
            new_compile_env.service_id = service.service_id
            new_compile_env.save()

    def __save_service_label(self, tenant, service, region, service_labels):
        service_label_list = []
        for service_label in service_labels:
            service_label.pop("ID")
            new_service_label = ServiceLabels(**service_label)
            new_service_label.tenant_id = tenant.tenant_id
            new_service_label.service_id = service.service_id
            new_service_label.region = region
            service_label_list.append(new_service_label)
        if service_label_list:
            ServiceLabels.objects.bulk_create(service_label_list)

    def __save_service_domain(self, service, service_domains):
        service_domain_list = []
        for domain in service_domains:
            domain.pop("ID")
            new_service_domain = ServiceDomain(**domain)
            new_service_domain.service_id = service.service_id
            service_domain_list.append(new_service_domain)
        if service_domain_list:
            ServiceDomain.objects.bulk_create(service_domain_list)

    def __save_service_event(self, tenant, service, service_events):
        event_list = []
        for event in service_events:
            event.pop("ID")
            new_service_event = ServiceEvent(**event)
            new_service_event.service_id = service.service_id
            new_service_event.tenant_id = tenant.tenant_id
            event_list.append(new_service_event)
        if event_list:
            ServiceEvent.objects.bulk_create(event_list)

    def __save_service_perms(self, service, service_perms):
        service_perm_list = []
        for service_perm in service_perms:
            service_perm.pop("ID")
            new_service_perm = ServiceRelPerms(**service_perm)
            new_service_perm.service_id = service.ID
            service_perm_list.append(new_service_perm)
        if service_perm_list:
            ServiceRelPerms.objects.bulk_create(service_perm_list)

    def __save_service_probes(self, service, service_probes):
        service_probe_list = []
        for probe in service_probes:
            probe.pop("ID")
            new_service_probe = ServiceProbe(**probe)
            new_service_probe.service_id = service.service_id
            service_probe_list.append(new_service_probe)
        if service_probe_list:
            ServiceProbe.objects.bulk_create(service_probe_list)

    def __save_service_source(self, tenant, service, service_source):
        if service_source:
            service_source.pop("ID")
            new_service_source = ServiceSourceInfo(**service_source)
            new_service_source.service_id = service.service_id
            new_service_source.team_id = tenant.tenant_id
            new_service_source.save()

    def __save_service_auth(self, service, service_auth):
        service_auth_list = []
        for auth in service_auth:
            auth.pop("ID")
            new_service_auth = TenantServiceAuth(**auth)
            new_service_auth.service_id = service.service_id
            service_auth_list.append(new_service_auth)
        if service_auth_list:
            TenantServiceAuth.objects.bulk_create(service_auth_list)

    def __save_service_image_relation(self, tenant, service, service_image_relation):
        if service_image_relation:
            service_image_relation.pop("ID")
            new_image_relation = ImageServiceRelation(**service_image_relation)
            new_image_relation.tenant_id = tenant.tenant_id
            new_image_relation.service_id = service.service_id
            new_image_relation.save()

    def __save_service_relations(self, tenant, service_relations_list, old_new_service_id_map):
        new_service_relation_list = []
        if service_relations_list:
            for relation in service_relations_list:
                relation.pop("ID")
                new_service_relation = TenantServiceRelation(**relation)
                new_service_relation.tenant_id = tenant.tenant_id
                new_service_relation.service_id = old_new_service_id_map[relation["service_id"]]
                new_service_relation.dep_service_id = old_new_service_id_map[relation["dep_service_id"]]
                new_service_relation_list.append(new_service_relation)
            TenantServiceRelation.objects.bulk_create(new_service_relation_list)

    def __save_service_mnt_relation(self, tenant, service_mnt_relation_list, old_new_service_id_map):
        new_service_mnt_relation_list = []
        if service_mnt_relation_list:
            for mnt in service_mnt_relation_list:
                mnt.pop("ID")
                new_service_mnt = TenantServiceMountRelation(**mnt)
                new_service_mnt.tenant_id = tenant.tenant_id
                new_service_mnt.service_id = old_new_service_id_map[mnt["service_id"]]
                new_service_mnt.dep_service_id = old_new_service_id_map[mnt["dep_service_id"]]
                new_service_mnt_relation_list.append(new_service_mnt)

            TenantServiceMountRelation.objects.bulk_create(new_service_mnt_relation_list)

    def update_migrate_original_group_id(self, old_original_group_id, new_original_group_id):
        migrate_repo.get_by_original_group_id(old_original_group_id).update(original_group_id=new_original_group_id)


migrate_service = GroupappsMigrateService()
