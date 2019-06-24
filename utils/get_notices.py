from backports.datetime_fromisoformat import MonkeyPatch
MonkeyPatch.patch_fromisoformat()
from datetime import datetime, timedelta
import logging
import os
import random
import re
import sys
import zipfile

from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)
SAM_API_KEY = os.getenv('SAM_API_KEY')
if not SAM_API_KEY:
    logger.critical("SAM_API_KEY not in env.")
    sys.exit(1)

def xstr(s):
    """[Converts objects to strings, treting NoneTypes as empty strings. Useful for dict.get() return values.]
    
    Arguments:
        s {[obj]} -- [any python object that can be converted to a string]
    
    Returns:
        [str] -- [s as a string.]
    """
    if s is None:
        return ''
    else:
        return str(s)


def get_random(n = 13):
    """[Generate a random string of n digits, with the first digit not equal to 0.]
    
    Keyword Arguments:
        n {int} -- [number of digits in randomly generated string] (default: {13})
    
    Returns:
        [str] -- [a string of n randomly generate integers]
    """
    start_n = str(random.randint(1,9))
    for _ in range(n-1):
        start_n += str(random.randint(0,9))
    
    return start_n


def get_now_minus_n(n):
    """[Returns a datetime string from n days ago, appended with '-04:00']
    
    Arguments:
        n {[int]} -- [the number of days to go back to]
    
    Returns:
        [str] -- [now_minus_n is a string representing a date n days ago]
    """
    now_minus_n = datetime.utcnow() - timedelta(n)
    now_minus_n = now_minus_n.strftime('%Y-%m-%d')
    #this is always appended to the time param, so we'll manually append it here
    now_minus_n += '-04:00'
    
    return now_minus_n


def api_get(uri, payload):
    """[requests.get wrapper with error handling that returns the json]
    
    Arguments:
        uri {[str]} -- [the uri to request]
        payload {[dict]} -- [a dict of params for the GET]
    
    Returns:
        [data] -- [dict, json-like]
    """
    try:
        #the headers dict will be merged with the default/session headers
        page = payload.get('page', 0)
        headers = {'origin': 'https://beta.sam.gov',
                   'referer': f'https://beta.sam.gov/search?keywords=&sort=-modifiedDate&index=opp&is_active=true&page={page}'}
        r = requests.get(uri, params = payload, headers = headers)
    except Exception as e:
        logger.critical(f"Exception in `get_opportunities` making GET request to {uri} with {payload}: \
                          {e}", exc_info=True)
        return
    if r.status_code != 200:
        logger.critical(f"Exception in `get_opportunities` making GET request to {uri} with {payload}: \
                        non-200 status code of {r.status_code}")
        return
    data = r.json()
    
    return data
    

def get_opportunities(modified_date = None, 
                      naics = ['334111', '334118', '3343', '33451', '334516', '334614', 
                              '5112', '518', '54169', '54121', '5415', '54169', '61142']):
    '''
    [Makes a GET request to the Get Opportunities API for a given procurement type (p_type) and date range.]
    
    Arguments:
        modified_date (str): [Format must be '%Y-%m-%d'. If None, defaults to three days ago.]
        naics (list): [a list of naics codes to use to filter the notices. Substrings will match.]
        
    Returns:
        data (dict): [the json response. See the API documentation for more detail.]
    '''
    modified_date_formatted = f'{modified_date}-04:00'
    modified_date = modified_date_formatted if modified_date else get_now_minus_n(3)
    payload = {'api_key': SAM_API_KEY,
               'random': get_random(),
               'index': 'opp',
               'is_active': 'true',
               'page':'0',
               'modified_date': modified_date,
              }
    uri = 'https://api.sam.gov/prod/sgs/v1/search/'
    data = api_get(uri, payload)
    if not data:
        return
    try:
        results = data['_embedded']['results']
    except KeyError:
        #no results!
        return
    total_pages = data['page']['totalPages']
    page = 1
    while page < total_pages:
        payload.update({'page': page})
        _data = api_get(uri, payload)
        if not _data:
            page += 1
            continue
        try:
            _results = _data['_embedded']['results']
        except KeyError:
            page += 1
            continue
        results.extend(_results)
        page += 1
    
    if naics:
        results = naics_filter_results(results, naics)
    
    return results

def naics_filter_results(results, naics):
    """[Given the results returned by get_opportunites, filter out those that don't match the desired naics]
    
    Arguments:
        results {[list]} -- [a list of sam opportunity api results]
        naics {[list]} -- [a list of naics to filter with]
    
    Returns:
        [list] -- [filtered_results is a subset of results]
    """
    filtered_results = []
    for result in results:
        naics_array = result.get('naics')
        notice_naics = get_classcod_naics(naics_array)
        if any(notice_naics.startswith(n) for n in naics):
            filtered_results.append(result)
    
    return filtered_results

def get_date_and_year(modified_date):
    """[Given the modifiedDate value in the API response, get the mmdd and yy values]
    
    Arguments:
        modified_date {[str]} -- [description]
    
    Returns:
        [tup] -- [tuple containing the date (mmdd) and year (yy) as strings]
    """
    modified_date_t_index = modified_date.find("T")
    date = modified_date[5:modified_date_t_index].replace("-",'')
    year = modified_date[2:4]
    return date, year

def proper_case(string):
    """[Given a string that's supposed to be a an agencys name (i.e. a proper noun), case it correctly.]
    
    Arguments:
        string {[str]} -- [a string of an agency's name, e.g. DEPARTMENT OF HOUSING AND URBAN DEVELOPMENT]
    
    Returns:
        [str] -- [string_proper, the proper-cased string, e.g. Department of Housing and Urban Development]
    """
    string_split = string.lower().split()
    string_proper = ''
    dont_caps = {'the', 'of', 'and'}
    for word in string_split:
        if word not in dont_caps:
            string_proper += f'{word.capitalize()} '
        else:
            string_proper += f'{word} '
    string_proper = string_proper.strip()
    
    return string_proper


def parse_agency_name(agency):
    """Convert the awkward agency string formats (e.g. HOMELAND SECURITY, DEPARTMENT OF --> Department of Homeland Security)
    
    Arguments:
        agency {[str]} -- [an agency's name]
    
    Returns:
        [str] -- [agency_name_proper, the proper-cased and formatted name of the agency]
    """
    agency = agency.strip()
    if not agency.isupper():
        return agency
    try:
        comma_index = agency.index(",")
    except ValueError:
        #because there's no comma
        agency = proper_case(agency)
        return agency
    agency_name = f'{agency[comma_index+2:]} {agency[:comma_index]}'.lower()
    agency_name_proper = proper_case(agency_name)
    
    return agency_name_proper


def get_agency_office_location_zip_offadd(organization_hierarchy):
    """[Extract geodata for the organizationHierarchy field of the api response]
    
    Arguments:
        organization_hierarchy {[list]} -- [a list of dictionaries (json array)]
    
    Returns:
        [tuple] -- [returns agency, office, location, zip_code, and offadd strings]
    """
    agency = ''
    office = ''
    location = ''
    zip_code = ''
    offadd = ''
    for i in organization_hierarchy:
        level = i.get('level')
        if level == 1:
            agency = xstr(i.get('name',''))
            agency = xstr(parse_agency_name(agency))
        elif level == 2:
            office = xstr(parse_agency_name(i.get('name','')))
        else:
            location = xstr(i.get('name'))
            address = i.get('address')
            if not address:
                continue
            zip_code = xstr(address.get('zip', ''))
            street_address = xstr(address.get('streetAddress', ''))
            street_address2 = xstr(address.get('streetAddress2', ''))
            city = xstr(address.get('city', ''))
            state = xstr(address.get('state', ''))
            offadd = f'{street_address} {street_address2} {city}, {state}'
            offadd = '' if offadd == '  , ' else offadd
            offadd = re.sub(r'  +',' ', offadd)

    return agency, office, location, zip_code, offadd

def get_classcod_naics(psc_naics):
    """Given an array of dictionaries representing either psc (classcod) or naics codes, extract the code. 
    
    Arguments:
        psc_naics {[list]} -- [a json array of dicts]
    
    Returns:
        [str] -- [the classification code or naics, depending on what was passed in]
    """
    if not psc_naics:
        return ''
    classcod_naics = max([xstr(i.get('code')) for i in psc_naics], key = len)
    
    return classcod_naics
 
def get_respdate(response_date):
    """[Given a date like "2019-04-16T15:00:00-04:00", return it in the '%m%d%y' format.]
    
    Arguments:
        response_date {[str]} -- [an ISO format date string like "2019-04-16T15:00:00-04:00"]
    
    Returns:
        [str] -- [date as a string in the '%m%d%y' format.]
    """
    if not response_date:
        return ''
    try:
        respdate = datetime.fromisoformat(response_date).strftime('%m%d%y')
    except ValueError as e:
        logger.warning(f"Error {e} parsing response_date of {response_date}", exc_info = True)
        return ''
    
    return respdate

def get_contact(point_of_contacts):
    """[Given the json array of pocs, extract a comma separated string with full names, job titles, and phone numbers.
    If there are multiple pocs, delimit them with a semicolon.]
    
    Arguments:
        point_of_contacts {[list]} -- [a json array of dictioanries for each point of contact]
    
    Returns:
        [str] -- [a comma separated string with full names, job titles, and phone numbers.
    If there are multiple pocs, delimit them with a semicolon.]
    """
    if not point_of_contacts:
        return ''
    
    full_names = [xstr(poc.get('fullName', '')) for poc in point_of_contacts]
    titles = [xstr(poc.get('title', '')) for poc in point_of_contacts]
    phones = [xstr(poc.get('phone', '')) for poc in point_of_contacts]
    contacts_list = [', '.join(map(str, i)) for i in zip(full_names, titles, phones)]
    contact = "; ".join(contacts_list)
    if contact.startswith(', '):
        #occurs when there's no name for the first poc, so try checking for others.
        try:
            full_name = next(s for s in full_names if s)
            ix = full_names.index(full_name)
            contact = contacts_list[ix]
        except StopIteration:
            #no full names
            contact = ''

    return contact

def get_description(descriptions):
    if not descriptions:
        return ''
    last_modified_dates = []
    for d in descriptions:
        last_modified_date = d.get('lastModifiedDate')
        if not last_modified_date:
            continue
        try:
            last_modified_date = datetime.fromisoformat(last_modified_date)
            last_modified_dates.append(last_modified_date)
        except ValueError as e:
            logger.warning(f"Error {e} parsing last_modified_date of {last_modified_date}", exc_info = True)
            continue
    if not last_modified_dates:
        description = max([desc.get('content', '') for desc in descriptions], key = len)
    else:
        max_date = max(last_modified_dates)
        max_date_i = last_modified_dates.index(max_date)
        description = descriptions[max_date_i].get('content','')
    
    return description
        
def get_text_from_html(text):
    if not text:
        return ''
    soup = BeautifulSoup(text, 'html.parser')
    # kill all script and style elements
    for script in soup(["script", "style"]):
        # rip it out
        script.extract()    
    # get text
    text = soup.get_text(separator = ' ')
    # break into lines and remove leading and trailing space on each
    lines = (line.strip() for line in text.splitlines())
    # break multi-headlines into a line each
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    # drop blank lines
    text = '\n'.join(chunk for chunk in chunks if chunk)

    return text

def get_setasides(setasides):
    setaside = ''
    if not setasides:
        setaside = 'N/A'
        return setaside
    if isinstance(setasides, list):
        for s in setasides:
            if not s:
                continue
            setaside += s.get('value', '') + ' '
    else:
        #must be a single dict
        setaside = setasides.get('value', '')
    setaside = setaside.strip()
    if not setaside:
        #empty strings are Falsy
        setaside = 'N/A'
    
    return setaside


def get_place_of_performance(place_of_performance):
    if not place_of_performance:
        return '','',''
    try:
        d = place_of_performance[0]
    except IndexError:
        return '','',''
    popzip = xstr(d.get('zip', ''))
    popcountry = xstr(d.get('country', ''))
    city = xstr(d.get('city', ''))
    street_address = xstr(d.get('streetAddress', ''))
    street_address2 = xstr(d.get('streetAddress2', ''))
    state = xstr(d.get('state', ''))
    popaddress = f'{street_address} {street_address2} {city}, {state}'
    popaddress = '' if popaddress == '  , ' else popaddress
    popaddress = re.sub(r'  +',' ', popaddress)
    
    return popzip, popcountry, popaddress   
        

def extract_emails(res):
    '''
    Given a json string representing a single opportunity notice, use an email re to find all the contact emails.
    
    Parameters:
        dumped_res (str): the result of json.dumps()
        
    Returns:
        emails (list): a list of unique email addresses
    '''
    pocs = res.get('pointOfContacts', [{'foo':'bar'}])
    matches = [xstr(poc.get('email')) for poc in pocs]
    if not any(matches):
        descriptions = res.get('descriptions')
        text_to_search = f'{pocs} {descriptions}'
        email_re = re.compile(r'[\w\.-]+@[\w\.-]+\.\w+')
        matches = re.findall(email_re, text_to_search)
    emails = list(set(matches))
    
    return emails
    

def schematize_results(results):
    """[Givent the results of the Get Opportunities API, convert the json to SRT's schema]
    
    Arguments:
        results {[list]} -- [json array of dictonaries for each result notice]
    
    Returns:
        notices {[dict]} -- [a dictionary with keys for the 3 notice types. Each value is an array of schematized notices]
    """
    notice_data = {'PRESOL': [],
                   'COMBINE': [],
                   'MOD': []}
    if not results:
        return notice_data
    for result in results:
        is_canceled = result.get('isCanceled')
        if is_canceled:
            continue
        modified_date = result.get('modifiedDate','')
        date, year = get_date_and_year(modified_date)
        organization_hierarchy = result.get('organizationHierarchy','')
        place_of_performance = result.get('placeOfPerformance', '')
        popzip, popcountry, popaddress = get_place_of_performance(place_of_performance)
        agency, office, location, zip_code, offadd = get_agency_office_location_zip_offadd(organization_hierarchy)
        psc = result.get('psc')
        classcod = get_classcod_naics(psc)
        _naics = result.get('naics')
        naics = get_classcod_naics(_naics)
        subject = result.get('title', '')
        solnbr = result.get('solicitationNumber','').lower().strip()
        response_date = result.get('responseDate')
        respdate = get_respdate(response_date)
        archive_date = result.get('archiveDate')
        archdate = get_respdate(archive_date)
        point_of_contacts = result.get('pointOfContacts')
        contact = get_contact(point_of_contacts)
        descriptions = result.get('descriptions')
        desc = get_text_from_html(get_description(descriptions))
        _id = result.get('_id')
        url = f'https://beta.sam.gov/opp/{_id}'
        setasides = result.get('solicitation',{'foo':'bar'}).get('setAside')
        setaside = get_setasides(setasides)
        notice = {'date': date,
                  'year': year,
                  'agency': agency,
                  'office': office,
                  'location': location,
                  'zip': zip_code,
                  'classcod': classcod,
                  'naics': naics,
                  'offadd': offadd,
                  'subject': subject,
                  'solnbr': solnbr,
                  'respdate': respdate,
                  'archdate': archdate,
                  'contact': contact,
                  'desc': desc,
                  'url': url,
                  'setaside': setaside,
                  'popzip': popzip,
                  'popcountry': popcountry,
                  'popaddress': popaddress
                 }
        emails = extract_emails(result)
        notice.update({'emails': emails})
        notice_type = result.get('type', {'foo':'bar'}).get('value')
        if notice_type == 'Combined Synopsis/Solicitation':
            notice_data['COMBINE'].append(notice)
        elif notice_type == 'Presolicitation':
            notice_data['PRESOL'].append(notice)
        elif notice_type == 'Modification/Amendment/Cancel':
            notice_data['MOD'].append(notice)
        else:
            if any(x in notice_type.lower() for x in {'presol', 'combine', 'modif'}):
                logger.warning(f"Found an unanticipated notice type of {notice_type} from {url}")
            
    return notice_data
            
def get_notices(modified_date = None):
    """[Get notices for a give modifiedDate using the SAM API and then schematize them.]
    
    Keyword Arguments:
        modified_date {[str]} -- [A date string in the '%Y-%m-%d' format. If None, modified date will
        default to 3 days ago.] (default: {None})
    
    Returns:
        notices {[dict]} -- [a dictionary with keys for the 3 notice types. Each value is an array of schematized notices]
    """
    results = get_opportunities(modified_date = modified_date)
    notice_data = schematize_results(results)
    
    return notice_data

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') 
    notice_data = get_notices()