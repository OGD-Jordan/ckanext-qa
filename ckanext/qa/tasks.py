'''
Provide some Quality Assurance by scoring datasets against Sir Tim
Berners-Lee\'s five stars of openness
'''
import sys
import datetime
import json
import os
import traceback

from ckan.common import _

from ckan.plugins import toolkit
import ckan.lib.helpers as ckan_helpers
from ckanext.qa.sniff_format import sniff_file_format
from ckanext.archiver.model import Archival, Status

import logging

log = logging.getLogger(__name__)

if sys.version_info[0] >= 3:
    unicode = str

if toolkit.check_ckan_version(max_version='2.6.99'):
    from ckan.lib import celery_app

    @celery_app.celery.task(name="qa.update_package")
    def update_package_celery(*args, **kwargs):
        update_package(*args, **kwargs)

    @celery_app.celery.task(name="qa.update")
    def update_celery(*args, **kwargs):
        update(*args, **kwargs)


class QAError(Exception):
    pass


# Description of each score, used elsewhere
OPENNESS_SCORE_DESCRIPTION = {
    0: 'Not obtainable or license is not open',
    1: 'Obtainable and open license',
    2: 'Machine readable format',
    3: 'Open and standardized format',
    4: 'Ontologically represented',
    5: 'Fully Linked Open Data as appropriate',
}


def update_package(package_id):
    """
    Given a package, calculates an openness score for each of its resources.
    It is more efficient to call this than 'update' for each resource.

    Returns None
    """

    try:
        log.debug('QA 5')

        update_package_(package_id)
    except Exception as e:
        log.error('Exception occurred during QA update_package: %s: %s',
                  e.__class__.__name__, unicode(e))
        raise


def update_package_(package_id):
    log.debug('QA 6')
    from ckan import model
    package = model.Package.get(package_id)
    if not package:
        raise QAError('Package ID not found: %s' % package_id)

    log.info('Openness scoring package %s (%i resources)', package.name,
             len(package.resources))

    for resource in package.resources:
        qa_result = resource_score(resource)
        log.info('Openness scoring: \n%r\n%r\n%r\n\n', qa_result, resource,
                 resource.url)
        save_qa_result(resource, qa_result)
        log.info('CKAN updated with openness score')

    # Refresh the index for this dataset, so that it contains the latest
    # qa info
    # _update_search_index(package.id)


def update(resource_id):
    """
    Given a resource, calculates an openness score.

    Returns a JSON dict with keys:

        'openness_score': score (int)
        'openness_score_reason': the reason for the score (string)
    """
    try:
        update_resource_(resource_id)
    except Exception as e:
        log.error('Exception occurred during QA update_resource: %s: %s',
                  e.__class__.__name__, unicode(e))
        raise


def update_resource_(resource_id):
    from ckan import model
    resource = model.Resource.get(resource_id)
    if not resource:
        raise QAError('Resource ID not found: %s' % resource_id)
    qa_result = resource_score(resource)
    log.info('Openness scoring: \n%r\n%r\n%r\n\n', qa_result, resource,
             resource.url)
    save_qa_result(resource, qa_result)
    log.info('CKAN updated with openness score')

    if toolkit.check_ckan_version(max_version='2.2.99'):
        package = resource.resource_group.package
    else:
        package = resource.package
    # if package:
    #     # Refresh the index for this dataset, so that it contains the latest
    #     # qa info
    #     _update_search_index(package.id)
    # else:
    #     log.warning('Resource not connected to a package. Res: %r', resource)
    return json.dumps(qa_result)


def get_qa_format(resource_id):
    '''Returns the format of the resource, as recorded in the QA table.'''
    from ckanext.qa.model import QA
    q = QA.get_for_resource(resource_id)
    if not q:
        return ''
    return q.format


def format_get(key):
    '''Returns a resource format, as defined in ckan.

    :param key: format extension / mimetype / title e.g. 'CSV',
                'application/msword', 'Word document'
    :param key: string
    :returns: format string
    '''
    format_tuple = ckan_helpers.resource_formats().get(key.lower())
    if not format_tuple:
        return
    return format_tuple[1]  # short name


def resource_score(resource):
    """
    Score resource on Sir Tim Berners-Lee\'s five stars of openness.

    Returns a dict with keys:

        'openness_score': score (int)
        'openness_score_reason': the reason for the score (string)
        'format': format of the data (string)
        'archival_timestamp': time of the archival that this result is based on (iso string)

    Raises QAError for reasonable errors
    """
    score = 0
    score_reason = ''
    format_ = None

    try:
        score_reasons = []  # a list of strings detailing how we scored it
        archival = Archival.get_for_resource(resource_id=resource.id)
        if not resource:
            raise QAError('Could not find resource "%s"' % resource.id)

        score, format_ = score_if_link_broken(archival, resource, score_reasons)
        if score is None:
            # we don't want to take the publisher's word for it, in case the link
            # is only to a landing page, so highest priority is the sniffed type
            score, format_ = score_by_sniffing_data(archival, resource,
                                                    score_reasons)
            if score is None:
                # Fall-backs are user-given data
                score, format_ = score_by_url_extension(resource, score_reasons)
                if score is None:
                    score, format_ = score_by_format_field(resource, score_reasons)
                    if score is None:
                        log.warning('Could not score resource: "%s" with url: "%s"',
                                    resource.id, resource.url)
                        score_reasons.append(_('Could not understand the file format, therefore score is 1.'))
                        score = 1
                        if format_ is None:
                            # use any previously stored format value for this resource
                            format_ = get_qa_format(resource.id)
        score_reason = ' '.join(score_reasons)
        format_ = format_ or None
    except Exception as e:
        log.error('Unexpected error while calculating openness score %s: %s\nException: %s',
                  e.__class__.__name__, unicode(e), traceback.format_exc())
        score_reason = _("Unknown error: %s") % str(e)
        raise

    # Even if we can get the link, we should still treat the resource
    # as having a score of 0 if the license isn't open.
    #
    # It is important we do this check after the link check, otherwise
    # the link checker won't get the chance to see if the resource
    # is broken.
    if toolkit.check_ckan_version(max_version='2.2.99'):
        package = resource.resource_group.package
    else:
        package = resource.package
    if score > 0 and not package.isopen():
        score_reason = _('License not open')
        score = 0

    log.info('Score: %s Reason: %s', score, score_reason)

    archival_updated = archival.updated.isoformat() \
        if archival and archival.updated else None
    result = {
        'openness_score': score,
        'openness_score_reason': score_reason,
        'format': format_,
        'archival_timestamp': archival_updated
    }

    return result


def broken_link_error_message(archival):
    '''Given an archival for a broken link, it returns a helpful
    error message (string) describing the attempts.'''

    def format_date(date):
        if date:
            return date.strftime('%d/%m/%Y')
        else:
            return ''

    messages = [_('File could not be downloaded.'),
                _('Reason') + ':', unicode(archival.status) + '.',
                _('Error details: %s.') % archival.reason,
                _('Attempted on %s.') % format_date(archival.updated)]
    last_success = format_date(archival.last_success)
    if archival.failure_count == 1:
        if last_success:
            messages.append(_('This URL last worked on: %s.') % last_success)
        else:
            messages.append(_('This was the first attempt.'))
    else:
        messages.append(_('Tried %s times since %s.') %
                        (archival.failure_count,
                         format_date(archival.first_failure)))
        if last_success:
            messages.append(_('This URL last worked on: %s.') % last_success)
        else:
            messages.append(_('This URL has not worked in the history of this tool.'))
    return ' '.join(messages)


def score_if_link_broken(archival, resource, score_reasons):
    '''
    Looks to see if the archiver said it was broken, and if so, writes to
    the score_reasons and returns a score.

    Return values:
      * Returns a tuple: (score, format_)
      * score is an integer or None if it cannot be determined
      * format_ is a string or None
      * is_broken is a boolean
    '''
    if archival and archival.is_broken:
        # Score 0 since we are sure the link is currently broken
        score_reasons.append(broken_link_error_message(archival))
        format_ = get_qa_format(resource.id)
        log.info('Archiver says link is broken. Previous format: %r' % format_)
        return (0, format_)
    return (None, None)


def score_by_sniffing_data(archival, resource, score_reasons):
    '''
    Looks inside a data file\'s contents to determine its format and score.

    It adds strings to score_reasons list about how it came to the conclusion.

    Return values:
      * It returns a tuple: (score, format_string)
      * If it cannot work out the format then format_string is None
      * If it cannot score it, then score is None
    '''
    from ckanext.qa.lib import resource_format_scores
    if not archival or not archival.cache_filepath:
        score_reasons.append(_('This file had not been downloaded at the time of scoring it.'))
        return (None, None)
    # Analyse the cached file
    filepath = archival.cache_filepath
    if not os.path.exists(filepath):
        score_reasons.append(_('Cache filepath does not exist: "%s".') % filepath)
        return (None, None)
    else:
        if filepath:
            sniffed_format = sniff_file_format(filepath)
            score = resource_format_scores().get(sniffed_format['format']) \
                if sniffed_format else None
            if sniffed_format:
                score_reasons.append(_('Content of file appeared to be format "%s" which receives openness score: %s.')
                                     % (sniffed_format['format'], score))
                return score, sniffed_format['format']
            else:
                score_reasons.append(_('The format of the file was not recognized from its contents.'))
                return (None, None)
        else:
            # No cache_url
            if archival.status_id == Status.by_text('Chose not to download'):
                score_reasons.append(_('File was not downloaded deliberately') + '. '
                                     + _('Reason') + ': %s. ' % archival.reason + _(
                    'Using other methods to determine file openness.'))
                return (None, None)
            elif archival.is_broken is None and archival.status_id:
                # i.e. 'Download failure' or 'System error during archival'
                score_reasons.append(_('A system error occurred during downloading this file') + '. '
                                     + _('Reason') + ': %s. ' % archival.reason + _(
                    'Using other methods to determine file openness.'))
                return (None, None)
            else:
                score_reasons.append(_('This file had not been downloaded at the time of scoring it.'))
                return (None, None)


def score_by_url_extension(resource, score_reasons):
    '''
    Looks at the URL for a resource to determine its format and score.

    It adds strings to score_reasons list about how it came to the conclusion.

    Return values:
      * It returns a tuple: (score, format_string)
      * If it cannot work out the format then format is None
      * If it cannot score it, then score is None
    '''
    from ckanext.qa.lib import resource_format_scores
    extension_variants_ = extension_variants(resource.url.strip())
    if not extension_variants_:
        score_reasons.append(_('Could not determine a file extension in the URL.'))
        return (None, None)
    for extension in extension_variants_:
        format_ = format_get(extension)
        if format_:
            score = resource_format_scores().get(format_)
            if score:
                score_reasons.append(
                    _('URL extension "%s" relates to format "%s" and receives score: %s.') % (extension, format_, score))
                return score, format_
            else:
                score = 1
                score_reasons.append(_('URL extension "%s" relates to format "%s"'
                                       ' but a score for that format is not configured, so giving it default score %s.')
                                     % (extension, format_, score))
                return score, format_
        score_reasons.append(_('URL extension "%s" is an unknown format.') % extension)
    return (None, None)


def extension_variants(url):
    '''
    Returns a list of extensions, in order of which would more
    significant.

    >>> extension_variants('http://dept.gov.uk/coins.data.1996.csv.zip')
    ['csv.zip', 'zip']
    >>> extension_variants('http://dept.gov.uk/data.csv?callback=1')
    ['csv']
    '''
    url = url.split('?')[0]  # get rid of params
    url = url.split('/')[-1]  # get rid of path - leaves filename
    split_url = url.split('.')
    results = []
    for number_of_sections in [2, 1]:
        if len(split_url) > number_of_sections:
            results.append('.'.join(split_url[-number_of_sections:]))
    return results


def score_by_format_field(resource, score_reasons):
    '''
    Looks at the format field of a resource to determine its format and score.

    It adds strings to score_reasons list about how it came to the conclusion.

    Return values:
      * It returns a tuple: (score, format_string)
      * If it cannot work out the format then format_string is None
      * If it cannot score it, then score is None
    '''
    from ckanext.qa.lib import resource_format_scores, munge_format_to_be_canonical
    format_field = resource.format or ''
    if not format_field:
        score_reasons.append(_('Format field is blank.'))
        return (None, None)
    format_tuple = ckan_helpers.resource_formats().get(format_field.lower()) or \
        ckan_helpers.resource_formats().get(munge_format_to_be_canonical(format_field))
    if not format_tuple:
        score_reasons.append(_('Format field "%s" does not correspond to a known format.') % format_field)
        return (None, None)
    score = resource_format_scores().get(format_tuple[1])
    score_reasons.append(_('Format field "%s" receives score: %s.') %
                         (format_field, score))
    return (score, format_tuple[1])


def _update_search_index(package_id):
    '''
    Tells CKAN to update its search index for a given package.
    '''
    from ckan import model
    from ckan.lib.search.index import PackageSearchIndex
    package_index = PackageSearchIndex()
    context_ = {'model': model, 'ignore_auth': True, 'session': model.Session,
                'use_cache': False, 'validate': False}
    package = toolkit.get_action('package_show')(context_, {'id': package_id})
    package_index.index_package(package, defer_commit=False)
    log.info('Search indexed %s', package['name'])


def save_qa_result(resource, qa_result):
    """
    Saves the results of the QA check to the qa table.
    """
    import ckan.model as model
    from ckanext.qa.model import QA

    now = datetime.datetime.now()

    qa = QA.get_for_resource(resource.id)
    if not qa:
        qa = QA.create(resource.id)
        model.Session.add(qa)
    else:
        log.info(u'QA from before: %r', qa)

    for key in ('openness_score', 'openness_score_reason', 'format'):
        setattr(qa, key, qa_result[key])
    qa.archival_timestamp = qa_result['archival_timestamp']
    qa.updated = now

    model.Session.commit()

    log.info('QA results updated ok')
    return qa  # for tests
