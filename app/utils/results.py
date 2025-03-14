from app.models.endpoint import Endpoint
from bs4 import BeautifulSoup, NavigableString
import copy
import html
import os
import urllib.parse as urlparse
from urllib.parse import parse_qs
import re

SKIP_ARGS = ['ref_src', 'utm']
SKIP_PREFIX = ['//www.', '//mobile.', '//m.']
GOOG_STATIC = 'www.gstatic.com'
GOOG_IMG = '/images/branding/searchlogo/1x/googlelogo'
LOGO_URL = GOOG_IMG + '_desk'
BLANK_B64 = ('data:image/png;base64,'
             'iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAQAAAAnOwc2AAAAD0lEQVR42mNkw'
             'AIYh7IgAAVVAAuInjI5AAAAAElFTkSuQmCC')

# Ad keywords
BLACKLIST = [
    'ad', 'anuncio', 'annuncio', 'annonce', 'Anzeige', '广告', '廣告', 'Reklama',
    'Реклама', 'Anunț', '광고', 'annons', 'Annonse', 'Iklan', '広告', 'Augl.',
    'Mainos', 'Advertentie', 'إعلان', 'Գովազդ', 'विज्ञापन', 'Reklam', 'آگهی',
    'Reklāma', 'Reklaam', 'Διαφήμιση', 'מודעה', 'Hirdetés', 'Anúncio'
]

SITE_ALTS = {
    'twitter.com': os.getenv('WHOOGLE_ALT_TW', 'farside.link/nitter'),
    'youtube.com': os.getenv('WHOOGLE_ALT_YT', 'farside.link/invidious'),
    'instagram.com': os.getenv('WHOOGLE_ALT_IG', 'farside.link/bibliogram/u'),
    'reddit.com': os.getenv('WHOOGLE_ALT_RD', 'farside.link/libreddit'),
    **dict.fromkeys([
        'medium.com',
        'levelup.gitconnected.com'
    ], os.getenv('WHOOGLE_ALT_MD', 'farside.link/scribe')),
    'imgur.com': os.getenv('WHOOGLE_ALT_IMG', 'imgin.voidnet.tech'),
    'wikipedia.com': os.getenv('WHOOGLE_ALT_WIKI', 'wikiless.org')
}


def bold_search_terms(response: str, query: str) -> BeautifulSoup:
    """Wraps all search terms in bold tags (<b>). If any terms are wrapped
    in quotes, only that exact phrase will be made bold.

    Args:
        response: The initial response body for the query
        query: The original search query

    Returns:
        BeautifulSoup: modified soup object with bold items
    """
    response = BeautifulSoup(response, 'html.parser')

    def replace_any_case(element: NavigableString, target_word: str) -> None:
        # Replace all instances of the word, but maintaining the same case in
        # the replacement
        if len(element) == len(target_word):
            return

        if not re.match('.*[a-zA-Z0-9].*', target_word) or (
                element.parent and element.parent.name == 'style'):
            return

        element.replace_with(BeautifulSoup(
            re.sub(fr'\b((?![{{}}<>-]){target_word}(?![{{}}<>-]))\b',
                   r'<b>\1</b>',
                   html.escape(element),
                   flags=re.I), 'html.parser')
        )

    # Split all words out of query, grouping the ones wrapped in quotes
    for word in re.split(r'\s+(?=[^"]*(?:"[^"]*"[^"]*)*$)', query):
        word = re.sub(r'[^A-Za-z0-9 ]+', '', word)
        target = response.find_all(
            text=re.compile(r'' + re.escape(word), re.I))
        for nav_str in target:
            replace_any_case(nav_str, word)

    return response


def has_ad_content(element: str) -> bool:
    """Inspects an HTML element for ad related content

    Args:
        element: The HTML element to inspect

    Returns:
        bool: True/False for the element containing an ad

    """
    return (element.upper() in (value.upper() for value in BLACKLIST)
            or 'ⓘ' in element)


def get_first_link(soup: BeautifulSoup) -> str:
    """Retrieves the first result link from the query response

    Args:
        soup: The BeautifulSoup response body

    Returns:
        str: A str link to the first result

    """
    # Replace hrefs with only the intended destination (no "utm" type tags)
    for a in soup.find_all('a', href=True):
        # Return the first search result URL
        if 'url?q=' in a['href']:
            return filter_link_args(a['href'])
    return ''


def get_site_alt(link: str) -> str:
    """Returns an alternative to a particular site, if one is configured

    Args:
        link: A string result URL to check against the SITE_ALTS map

    Returns:
        str: An updated (or ignored) result link

    """
    # Need to replace full hostname with alternative to encapsulate
    # subdomains as well
    hostname = urlparse.urlparse(link).hostname

    for site_key in SITE_ALTS.keys():
        if not hostname or site_key not in hostname:
            continue

        link = link.replace(hostname, SITE_ALTS[site_key])
        for prefix in SKIP_PREFIX:
            link = link.replace(prefix, '//')
        break

    return link


def filter_link_args(link: str) -> str:
    """Filters out unnecessary URL args from a result link

    Args:
        link: The string result link to check for extraneous URL params

    Returns:
        str: An updated (or ignored) result link

    """
    parsed_link = urlparse.urlparse(link)
    link_args = parse_qs(parsed_link.query)
    safe_args = {}

    if len(link_args) == 0 and len(parsed_link) > 0:
        return link

    for arg in link_args.keys():
        if arg in SKIP_ARGS:
            continue

        safe_args[arg] = link_args[arg]

    # Remove original link query and replace with filtered args
    link = link.replace(parsed_link.query, '')
    if len(safe_args) > 0:
        link = link + urlparse.urlencode(safe_args, doseq=True)
    else:
        link = link.replace('?', '')

    return link


def append_nojs(result: BeautifulSoup) -> None:
    """Appends a no-Javascript alternative for a search result

    Args:
        result: The search result to append a no-JS link to

    Returns:
        None

    """
    nojs_link = BeautifulSoup(features='html.parser').new_tag('a')
    nojs_link['href'] = f'/{Endpoint.window}?location=' + result['href']
    nojs_link.string = ' NoJS Link'
    result.append(nojs_link)


def add_ip_card(html_soup: BeautifulSoup, ip: str) -> BeautifulSoup:
    """Adds the client's IP address to the search results
        if query contains keywords

    Args:
        html_soup: The parsed search result containing the keywords
        ip: ip address of the client

    Returns:
        BeautifulSoup

    """
    if (not html_soup.select_one(".EY24We")
            and html_soup.select_one(".OXXup").get_text().lower() == "all"):
        # HTML IP card tag
        ip_tag = html_soup.new_tag("div")
        ip_tag["class"] = "ZINbbc xpd O9g5cc uUPGi"

        # For IP Address html tag
        ip_address = html_soup.new_tag("div")
        ip_address["class"] = "kCrYT ip-address-div"
        ip_address.string = ip

        # Text below the IP address
        ip_text = html_soup.new_tag("div")
        ip_text.string = "Your public IP address"
        ip_text["class"] = "kCrYT ip-text-div"

        # Adding all the above html tags to the IP card
        ip_tag.append(ip_address)
        ip_tag.append(ip_text)

        # Finding the element before which the IP card would be placed
        f_link = html_soup.select_one(".BNeawe.vvjwJb.AP7Wnd")
        ref_element = f_link.find_parent(class_="ZINbbc xpd O9g5cc" +
                                                " uUPGi")

        # Inserting the element
        ref_element.insert_before(ip_tag)
    return html_soup


def check_currency(response: str) -> dict:
    """Check whether the results have currency conversion

    Args:
        response: Search query Result

    Returns:
        dict: Consists of currency names and values

    """
    soup = BeautifulSoup(response, 'html.parser')
    currency_link = soup.find('a', {'href': 'https://g.co/gfd'})
    if currency_link:
        while 'class' not in currency_link.attrs or \
                'ZINbbc' not in currency_link.attrs['class']:
            currency_link = currency_link.parent
        currency_link = currency_link.find_all(class_='BNeawe')
        currency1 = currency_link[0].text
        currency2 = currency_link[1].text
        currency1 = currency1.rstrip('=').split(' ', 1)
        currency2 = currency2.split(' ', 1)
        if currency2[0][-3] == ',':
            currency1[0] = currency1[0].replace('.', '')
            currency1[0] = currency1[0].replace(',', '.')
            currency2[0] = currency2[0].replace('.', '')
            currency2[0] = currency2[0].replace(',', '.')
        else:
            currency1[0] = currency1[0].replace(',', '')
            currency2[0] = currency2[0].replace(',', '')
        return {'currencyValue1': float(currency1[0]),
                'currencyLabel1': currency1[1],
                'currencyValue2': float(currency2[0]),
                'currencyLabel2': currency2[1]
                }
    return {}


def add_currency_card(soup: BeautifulSoup,
                      conversion_details: dict) -> BeautifulSoup:
    """Adds the currency conversion boxes
    to response of the search query

    Args:
        soup: Parsed search result
        conversion_details: Dictionary of currency
        related information

    Returns:
        BeautifulSoup
    """
    # Element before which the code will be changed
    # (This is the 'disclaimer' link)
    element1 = soup.find('a', {'href': 'https://g.co/gfd'})

    while 'class' not in element1.attrs or \
            'nXE3Ob' not in element1.attrs['class']:
        element1 = element1.parent

    # Creating the conversion factor
    conversion_factor = (conversion_details['currencyValue1'] /
                         conversion_details['currencyValue2'])

    # Creating a new div for the input boxes
    conversion_box = soup.new_tag('div')
    conversion_box['class'] = 'conversion_box'

    # Currency to be converted from
    input_box1 = soup.new_tag('input')
    input_box1['id'] = 'cb1'
    input_box1['type'] = 'number'
    input_box1['class'] = 'cb'
    input_box1['value'] = conversion_details['currencyValue1']
    input_box1['oninput'] = f'convert(1, 2, {1 / conversion_factor})'

    label_box1 = soup.new_tag('label')
    label_box1['for'] = 'cb1'
    label_box1['class'] = 'cb_label'
    label_box1.append(conversion_details['currencyLabel1'])

    br = soup.new_tag('br')

    # Currency to be converted to
    input_box2 = soup.new_tag('input')
    input_box2['id'] = 'cb2'
    input_box2['type'] = 'number'
    input_box2['class'] = 'cb'
    input_box2['value'] = conversion_details['currencyValue2']
    input_box2['oninput'] = f'convert(2, 1, {conversion_factor})'

    label_box2 = soup.new_tag('label')
    label_box2['for'] = 'cb2'
    label_box2['class'] = 'cb_label'
    label_box2.append(conversion_details['currencyLabel2'])

    conversion_box.append(input_box1)
    conversion_box.append(label_box1)
    conversion_box.append(br)
    conversion_box.append(input_box2)
    conversion_box.append(label_box2)

    element1.insert_before(conversion_box)
    return soup


def get_tabs_content(tabs: dict,
                     full_query: str,
                     search_type: str,
                     translation: dict) -> dict:
    """Takes the default tabs content and updates it according to the query.

    Args:
        tabs: The default content for the tabs
        full_query: The original search query
        search_type: The current search_type
        translation: The translation to get the names of the tabs

    Returns:
        dict: contains the name, the href and if the tab is selected or not
    """
    tabs = copy.deepcopy(tabs)
    for tab_id, tab_content in tabs.items():
        # update name to desired language
        if tab_id in translation:
            tab_content['name'] = translation[tab_id]

        # update href with query
        query = full_query.replace(f'&tbm={search_type}', '')

        if tab_content['tbm'] is not None:
            query = f"{query}&tbm={tab_content['tbm']}"

        tab_content['href'] = tab_content['href'].format(query=query)

        # update if selected tab (default all tab is selected)
        if tab_content['tbm'] == search_type:
            tabs['all']['selected'] = False
            tab_content['selected'] = True
    return tabs
