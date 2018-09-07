
# -*- coding: utf8 -*-
"""
  Created on 18/8/27.
"""
import logging

from console.models.services import ServiceGroup
from console.models.services import ServiceGroupRelation
from console.repositories.app import service_repo
logger = logging.getLogger("default")


class SyncTenantServiceManager(object):
    """更新服务表，主要有tenant_service 表中create_status(创建状态)，
        docker_cmd命令，
        team_gitlab_info表中的数据
    """

    def sync_service_info(self):
        try:
            print "start process ..."
            pos = 0
            NUMBER_OF_SERVICES = 300
            flag = True
            while flag:
                start_index = pos * NUMBER_OF_SERVICES
                services = self.get_limited_services(start_index, NUMBER_OF_SERVICES)
                if len(list(services)) == 0:
                    logger.debug("- process finished")
                    print ("- process finished")
                    flag = False

                for s in services:
                    # if s.tenant_id=="775194376af44bd89d835aa2241c821b":
                    self.process(s)
                logger.debug("finish process {0} data".format(NUMBER_OF_SERVICES * (pos + 1)))
                print "finish process {0} data".format(NUMBER_OF_SERVICES * (pos + 1))
                pos += 1
            print "process finished"
        except Exception as e:
            print e
            logger.exception(e)

    def process(self,service):
        is_service_ungrouped = self.is_service_ungrouped(service)
        if is_service_ungrouped:
            group = self.get_or_create_default_group(service.tenant_id,service.service_region)
            self.add_service_to_default_app(group.ID,service)

    def get_limited_services(self, start_index, number_of_services):
        query_sql = """ select * from tenant_service WHERE ID > 0 limit {0},{1}""".format(str(start_index), str(number_of_services))
        services = service_repo.get_services_by_raw_sql(query_sql)
        return services

    def is_service_ungrouped(self, service):
        """查询应用是否在关系表中"""
        results = ServiceGroupRelation.objects.filter(service_id=service.service_id)
        if not results:
            return True
        return False

    def get_or_create_default_group(self, tenant_id, region_name):
        # 查询是否有团队在当前数据中心是否有默认组，没有创建
        group = ServiceGroup.objects.filter(tenant_id=tenant_id, region_name=region_name, is_default=True).first()
        if not group:
            group = ServiceGroup.objects.create(tenant_id=tenant_id, region_name=region_name, group_name='默认组',
                                                is_default=True)
        return group

    def add_service_to_default_app(self, group_id, service):
        sgr = ServiceGroupRelation.objects.create(service_id=service.service_id, group_id=group_id,
                                                  tenant_id=service.tenant_id,
                                                  region_name=service.service_region)
        return sgr


syncManager = SyncTenantServiceManager()