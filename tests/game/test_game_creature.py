"""Synthetic inference responses only; no model, headset or interactive approval."""
import json
import unittest
from unittest.mock import Mock
from agent.services.game_creature import CreaturePlanner, clean
from agent.services.game_dragon import DragonError


class CreaturePlannerTests(unittest.TestCase):
    def request(self):
        return {'schema_version':'1.0','instruction':'Mach den Kopf größer','context':{'base_revision':3,'selected_parts':[
            {'id':'head','locked':False,'protected_fraction':0,'bounds':[[-.5,1,-2],[.5,2,-1]]}]}}

    def planner(self, decision):
        self.post = Mock(return_value=json.dumps({'results':[{'decision':decision,'fields':{'speech':{'generated':True}}}]}).encode())
        return CreaturePlanner('http://127.0.0.1:18150',self.post)

    def test_plan_is_local_bounded_and_selection_owned_by_hub(self):
        result = self.planner({'tool':'larger','region':'head','amount':'0.2','speech':'Ein breiterer Kopf.'}).plan(self.request())
        self.assertEqual(result['operations'][0]['value'],1.2)
        body = json.loads(self.post.call_args.args[1])
        self.assertEqual(body['schema']['region']['choices'],['head'])
        self.assertEqual(self.post.call_args.args[0],'http://127.0.0.1:18150/v1/decision')
        self.assertEqual(body['mode'],'tree')

    def test_generated_template_parameters_are_whitelisted(self):
        result = self.planner({'template':'dragon','body':'1.3','neck':'0.8','wings':'4.0','tail':'2.0','horns':'2','speech':'Ein Drache mit großen Flügeln.'}).plan(
            {'schema_version':'1.0','mode':'generate','instruction':'Großer Drache'})
        self.assertEqual(result['generation']['parameters']['wing_span'],4.)
        self.assertEqual(result['generation']['parameters']['horn_count'],2)

    def test_untrusted_instruction_cannot_select_regions_or_programs(self):
        for field,value in [('tool','exec'),('region','world'),('amount','nan')]:
            decision={'tool':'smooth','region':'head','amount':'0.2','speech':'Vorschlag'}; decision[field]=value
            with self.assertRaises(DragonError): self.planner(decision).plan(self.request())
        data = self.request(); data['context']['selected_parts'][0]['bounds'][0][0]=float('nan')
        with self.assertRaises(DragonError): clean(data)

    def test_mask_blocks_generated_appendage(self):
        data=self.request(); data['context']['selected_parts'][0]['protected_fraction']=.1
        with self.assertRaises(DragonError): self.planner({'tool':'add_horn','region':'head','amount':'0.2','speech':'Horn'}).plan(data)
