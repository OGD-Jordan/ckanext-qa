import logging

import ckan.plugins.toolkit as tk
from ckanext.archiver.model import Archival
from ckanext.qa.model import QA, aggregate_qa_for_a_dataset
from ckanext.qa import tasks

log = logging.getLogger(__name__)
_ = tk._


@tk.side_effect_free
def qa_resource_show(context, data_dict):
    '''
    Returns the QA and Archival information for a package or resource.
    '''
    model = context['model']
    tk.check_access('qa_resource_show', context, data_dict)

    res_id = tk.get_or_bust(data_dict, 'id')
    res = model.Resource.get(res_id)
    if not res:
        raise tk.ObjectNotFound

    archival = Archival.get_for_resource(res_id)
    qa = QA.get_for_resource(res_id)
    pkg = res.package
    return_dict = {
        'name': pkg.name,
        'title': pkg.title,
        'id': res.id
        }
    return_dict['archival'] = archival.as_dict()
    return_dict.update(qa.as_dict())
    return return_dict


@tk.side_effect_free
def qa_package_openness_show(context, data_dict):
    '''
    Returns the QA score for a package, aggregating the
    scores of its resources.
    '''
    model = context['model']
    tk.check_access('qa_package_openness_show', context, data_dict)

    dataset_id = tk.get_or_bust(data_dict, 'id')
    dataset = model.Package.get(dataset_id)
    if not dataset:
        raise tk.ObjectNotFound

    qa_objs = QA.get_for_package(dataset.id)
    qa_dict = aggregate_qa_for_a_dataset(qa_objs)
    return qa_dict


def qa_package_openness_show_or_initiate(context, data_dict):
    dataset_id = tk.get_or_bust(data_dict, 'id')
    tk.check_access('qa_package_openness_show_or_initiate', context, data_dict)
    
    model = context['model']

    package = model.Package.get(dataset_id)

    if not package:
        raise tk.ObjectNotFound
    
    qa_objs = QA.get_for_package(package.id)
    
    if not qa_objs or len(qa_objs) != len(package.resources_all):
        tasks.update_package(package.id)


    return qa_package_openness_show(context, data_dict)



def qa_resource_openness_show_or_initiate(context, data_dict):
    tk.check_access('qa_resource_openness_show_or_initiate', context, data_dict)
    
    model = context['model']

    res_id = tk.get_or_bust(data_dict, 'id')
    res = model.Resource.get(res_id)
    if not res:
        raise tk.ObjectNotFound

    tk.get_action('archiver_resource_show_or_initiate')(
        {**context, 'ignore_auth': True}, 
        data_dict
        )
    
    qa = QA.get_for_resource(res_id)
    if not qa:
        tasks.update(res.id)

    return qa_resource_show(context, data_dict)
