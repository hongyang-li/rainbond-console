# -*- coding: utf-8 -*-
import datetime

from console.repositories.apply_repo import apply_repo
from console.repositories.team_repo import team_repo
from console.repositories.user_repo import user_repo


class ApplyService(object):

    def create_applicants(self, user_id, team_name):
        applicant = apply_repo.get_applicants_by_id_team_name(user_id=user_id, team_name=team_name)
        if not applicant:
            team = team_repo.get_team_by_team_name(team_name=team_name)
            user = user_repo.get_by_user_id(user_id=user_id)
            info = {
                "user_id": user_id,
                "user_name": user.get_username(),
                "team_id": team.tenant_id,
                "team_name": team_name,
                "team_alias": team.tenant_alias,
                "apply_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            apply_repo.create_apply_info(**info)
            return info
        else:
            return None


apply_service = ApplyService()
