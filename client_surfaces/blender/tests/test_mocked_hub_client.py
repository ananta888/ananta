from __future__ import annotations

from client_surfaces.blender.addon.client import BlenderHubClient
from client_surfaces.blender.addon.operators import submit_blender_goal


def test_mocked_hub_client_health_and_goal_submit():
    c=BlenderHubClient('http://localhost:5000')
    h=c.health()
    assert h['status']=='ok'
    res=submit_blender_goal('Improve material',{'scene':{'name':'S'}},'blender.scene.plan.v1')
    assert res['status']=='accepted'
