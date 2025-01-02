import json
import logging

import ckan.model as model
import ckan.plugins as p
import ckan.plugins.toolkit as tk

from ckanext.archiver.interfaces import IPipe
from ckanext.qa.logic import action, auth
from ckanext.qa.model import QA, aggregate_qa_for_a_dataset
from ckanext.qa.helpers import qa_openness_stars_resource_html, qa_openness_stars_dataset_html
from ckanext.qa.lib import create_qa_update_package_task


log = logging.getLogger(__name__)


from ckanext.qa.plugin.flask_plugin import MixinPlugin


class QAPlugin(MixinPlugin, p.SingletonPlugin):
    p.implements(p.IConfigurer, inherit=True)
    p.implements(IPipe, inherit=True)
    p.implements(p.IActions)
    p.implements(p.IAuthFunctions)
    p.implements(p.ITemplateHelpers)
    p.implements(p.IPackageController, inherit=True)


    # IConfigurer

    def update_config(self, config):
        tk.add_template_directory(config, '../templates')


    # IPipe

    def receive_data(self, operation, queue, **params):
        print('QA1')
        '''Receive notification from ckan-archiver that a dataset has been
        archived.'''
        print('QA2', operation)
        if not operation == 'package-archived':
            return
        dataset_id = params['package_id']

        dataset = model.Package.get(dataset_id)
        assert dataset

        print('QA3')
        create_qa_update_package_task(dataset, queue=queue)


    # IActions

    def get_actions(self):
        return {
            'qa_resource_show': action.qa_resource_show,
            'qa_package_openness_show': action.qa_package_openness_show,
            'qa_package_openness_show_or_initiate': action.qa_package_openness_show_or_initiate,
            'qa_resource_openness_show_or_initiate': action.qa_resource_openness_show_or_initiate,
            }


    # IAuthFunctions

    def get_auth_functions(self):
        return {
            'qa_resource_show': auth.qa_resource_show,
            'qa_package_openness_show': auth.qa_package_openness_show,
            'qa_package_openness_show_or_initiate': auth.qa_package_openness_show_or_initiate,
            'qa_resource_openness_show_or_initiate': auth.qa_resource_openness_show_or_initiate,
            
            }


    # ITemplateHelpers

    def get_helpers(self):
        return {
            'qa_openness_stars_resource_html': qa_openness_stars_resource_html,
            'qa_openness_stars_dataset_html': qa_openness_stars_dataset_html,
            }


    # IPackageController

    def _add_to_pkg_dict(self, pkg_dict):
        qa_objs = QA.get_for_package(pkg_dict['id'])
        if not qa_objs:
            return
        
        # dataset
        dataset_qa = aggregate_qa_for_a_dataset(qa_objs)
        pkg_dict['qa'] = dataset_qa

        # resources
        qa_by_res_id = dict((a.resource_id, a) for a in qa_objs)
        for res in pkg_dict['resources']:
            qa = qa_by_res_id.get(res['id'])
            if qa:
                qa_dict = qa.as_dict()
                del qa_dict['id']
                del qa_dict['package_id']
                del qa_dict['resource_id']
                res['qa'] = qa_dict

    
    # def before_dataset_view(self, pkg_dict):
    #     self._add_to_pkg_dict(pkg_dict)
    #     return pkg_dict
    

    def after_dataset_show(self, context, pkg_dict):
        # Insert the qa info into the package_dict so that it is
        # available on the API.
        # When you edit the dataset, these values will not show in the form,
        # it they will be saved in the resources (not the dataset). I can't see
        # and easy way to stop this, but I think it is harmless. It will get
        # overwritten here when output again.

        self._add_to_pkg_dict(pkg_dict)

        if pkg_dict and pkg_dict['type'] == 'dataset' and pkg_dict.get('id'):
            extras = pkg_dict.get('extras',[])
            if 'qa' in pkg_dict:
                extras.append({
                    'id': 'qa-openness_score',
                    'package_id': pkg_dict['id'],
                    'key': 'openness_score',
                    'value': pkg_dict['qa']['openness_score'],
                    'state': 'active',
                })
                extras.append({
                    'id': 'qa-openness_score_reason',
                    'package_id': pkg_dict['id'],
                    'key': 'openness_score_reason',
                    'value': pkg_dict['qa']['openness_score_reason'],
                    'state': 'active',
                })
                
            pkg_dict['extras'] = extras


    def before_dataset_index(self, pkg_dict):
        qa_objs = QA.get_for_package(pkg_dict['id'])
        if not qa_objs: return pkg_dict

        dataset_qa = aggregate_qa_for_a_dataset(qa_objs)
        pkg_dict['openness_score'] = dataset_qa['openness_score']

        for key, value in pkg_dict.items():
            if isinstance(value, dict):
                pkg_dict[key] = json.dumps(value)

        return pkg_dict

