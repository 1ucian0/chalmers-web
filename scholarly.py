#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""scholarly.py"""

from __future__ import absolute_import, division, print_function, unicode_literals

from bs4 import BeautifulSoup

import arrow
import bibtexparser
import codecs
import hashlib
import pprint
import random
import re
import requests
import sys
import time

_GOOGLEID = hashlib.md5(str(random.random()).encode('utf-8')).hexdigest()[:16]
_COOKIES = {'GSP': 'ID={0}:CF=4'.format(_GOOGLEID)}
_HEADERS = {
    'accept-language': 'en-US,en',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Ubuntu Chromium/41.0.2272.76 Chrome/41.0.2272.76 Safari/537.36',
    'accept': 'text/html,application/xhtml+xml,application/xml'
    }
_SCHOLARHOST = 'https://scholar.google.com'
_PUBSEARCH = '/scholar?q={0}'
_AUTHSEARCH = '/citations?view_op=search_authors&hl=en&mauthors={0}'
_KEYWORDSEARCH = '/citations?view_op=search_authors&hl=en&mauthors=label:{0}'
_CITATIONAUTH = '/citations?user={0}&hl=en'
_CITATIONAUTHRE = r'user=([\w-]*)'
_CITATIONPUB = '/citations?view_op=view_citation&citation_for_view={0}'
_CITATIONPUBRE = r'citation_for_view=([\w-]*:[\w-]*)'
_SCHOLARPUB = '/scholar?oi=bibs&hl=en&cites={0}'
_SCHOLARPUBRE = r'cites=([\w-]*)'
_SCHOLARCITERE = r'gs_ocit\(event,\'([\w-]*)\''
_SESSION = requests.Session()
_PAGESIZE = 100


def _get_page(pagerequest):
    """Return the data for a page on scholar.google.com"""
    # Note that we include a sleep to avoid overloading the scholar server
    time.sleep(5+random.uniform(0, 5))
    resp_url = _SESSION.get(_SCHOLARHOST+pagerequest, headers=_HEADERS, cookies=_COOKIES)
    if resp_url.status_code == 200:
        return resp_url.text
    if resp_url.status_code == 503:
        # Inelegant way of dealing with the G captcha
        dest_url = requests.utils.quote(_SCHOLARHOST+pagerequest)
        g_id_soup = BeautifulSoup(resp_url.text, 'html.parser')
        g_id = g_id_soup.findAll('input')[1].get('value')
        # Get the captcha image
        captcha_url = _SCHOLARHOST+'/sorry/image?id={0}'.format(g_id)
        captcha = _SESSION.get(captcha_url, headers=_HEADERS)
        # Upload to remote host and display to user for human verification
        img_upload = requests.post('http://postimage.org/',
            files={'upload[]': ('scholarly_captcha.jpg', captcha.text)})
        img_url_soup = BeautifulSoup(img_upload.text, 'html.parser')
        img_url = img_url_soup.findAll(alt='scholarly_captcha')[0].get('src')
        print('CAPTCHA image URL: {0}'.format(img_url))
        # Need to check Python version for input
        if sys.version[0]=="3":
            g_response = input('Enter CAPTCHA: ')
        else:
            g_response = raw_input('Enter CAPTCHA: ')
        # Once we get a response, follow through and load the new page.
        url_response = _SCHOLARHOST+'/sorry/CaptchaRedirect?continue={0}&id={1}&captcha={2}&submit=Submit'.format(dest_url, g_id, g_response)
        resp_captcha = _SESSION.get(url_response, headers=_HEADERS)
        print('Forwarded to {0}'.format(resp_captcha.url))
        return _get_page(re.findall(r'https:\/\/(?:.*?)(\/.*)', resp_captcha.url)[0])
    else:
        raise Exception('Error: {0} {1}'.format(resp_url.status_code, resp_url.reason))


def _get_soup(pagerequest):
    """Return the BeautifulSoup for a page on scholar.google.com"""
    html = _get_page(pagerequest)
    return BeautifulSoup(html, 'html.parser')


def _search_scholar_soup(soup):
    """Generator that returns Publication objects from the search page"""
    while True:
        for row in soup.findAll('div', 'gs_r'):
            yield Publication(row, 'scholar')
        if soup.find(class_='gs_ico gs_ico_nav_next'):
            soup = _get_soup(soup.find(class_='gs_ico gs_ico_nav_next').parent['href'])
        else:
            break


def _search_citation_soup(soup):
    """Generator that returns Author objects from the author search page"""
    while True:
        for row in soup.findAll('div', 'gsc_1usr'):
            yield Author(row)
        nextbutton = soup.find(class_='gs_btnPR gs_in_ib gs_btn_half gs_btn_srt')
        if nextbutton and 'disabled' not in nextbutton.attrs:
            next_url = nextbutton['onclick'][17:-1]
            next_url = codecs.getdecoder("unicode_escape")(next_url)[0]
            soup = _get_soup(next_url)
        else:
            break


class Publication(object):
    """Returns an object for a single publication"""
    def __init__(self, __data, pubtype=None):
        self.bib = dict()
        self.source = pubtype
        if self.source == 'citations':
            self.bib['title'] = __data.find('a', class_='gsc_a_at').text
            self.id_citations = re.findall(_CITATIONPUBRE, __data.find('a', class_='gsc_a_at')['href'])[0]
            citedby = __data.find(class_='gsc_a_ac')
            if citedby and not citedby.text.isspace():
                self.citedby = int(citedby.text)
            year = __data.find(class_='gsc_a_h')
            if year and year.text and not year.text.isspace() and len(year.text)>0:
                self.bib['year'] = int(year.text)
        elif self.source == 'scholar':
            databox = __data.find('div', class_='gs_ri')
            title = databox.find('h3', class_='gs_rt')
            if title.find('span', class_='gs_ctu'): # A citation
                title.span.extract()
            elif title.find('span', class_='gs_ctc'): # A book or PDF
                title.span.extract()
            self.bib['title'] = title.text.strip()
            if title.find('a'):
                self.bib['url'] = title.find('a')['href']
            authorinfo = databox.find('div', class_='gs_a')
            self.bib['author'] = ' and '.join([i.strip() for i in authorinfo.text.split(' - ')[0].split(',')])
            if databox.find('div', class_='gs_rs'):
                self.bib['abstract'] = databox.find('div', class_='gs_rs').text
                if self.bib['abstract'][0:8].lower() == 'abstract':
                    self.bib['abstract'] = self.bib['abstract'][9:].strip()
            lowerlinks = databox.find('div', class_='gs_fl').find_all('a')
            for link in lowerlinks:
                if 'Import into BibTeX' in link.text:
                    self.url_scholarbib = link['href']
                if 'Cited by' in link.text:
                    self.citedby = int(re.findall(r'\d+', link.text)[0])
                    self.id_scholarcitedby = re.findall(_SCHOLARPUBRE, link['href'])[0]
            if __data.find('div', class_='gs_ggs gs_fl'):
                self.bib['eprint'] = __data.find('div', class_='gs_ggs gs_fl').find('a')['href']
        self._filled = False

    def fill(self):
        """Populate the Publication with information from its profile"""
        if self.source == 'citations':
            url_citations = _CITATIONPUB.format(self.id_citations)
            soup = _get_soup(url_citations)
            self.bib['title'] = soup.find('div', id='gsc_title').text
            if soup.find('a', class_='gsc_title_link'):
                self.bib['url'] = soup.find('a', class_='gsc_title_link')['href']
            for item in soup.findAll('div', class_='gs_scl'):
                key = item.find(class_='gsc_field').text
                val = item.find(class_='gsc_value')
                if key == 'Authors':
                    self.bib['author'] = ' and '.join([i.strip() for i in val.text.split(',')])
                elif key == 'Journal':
                    self.bib['journal'] = val.text
                elif key == 'Volume':
                    self.bib['volume'] = val.text
                elif key == 'Issue':
                    self.bib['number'] = val.text
                elif key == 'Pages':
                    self.bib['pages'] = val.text
                elif key == 'Publisher':
                    self.bib['publisher'] = val.text
                elif key == 'Publication date':
                    self.bib['year'] = arrow.get(val.text).year
                elif key == 'Description':
                    if val.text[0:8].lower() == 'abstract':
                        val = val.text[9:].strip()
                    self.bib['abstract'] = val
                elif key == 'Total citations':
                    self.id_scholarcitedby = re.findall(_SCHOLARPUBRE, val.find('a')['href'])[0]
            if soup.find('div', class_='gsc_title_ggi'):
                self.bib['eprint'] = soup.find('div', class_='gsc_title_ggi').a['href']
            self._filled = True
        elif self.source == 'scholar':
            bibtex = _get_page(self.url_scholarbib)
            self.bib.update(bibtexparser.loads(bibtex).entries[0])
            self._filled = True
        return self

    def get_citedby(self):
        """Searches GScholar for other articles that cite this Publication and
        returns a Publication generator.
        """
        if not hasattr(self, 'id_scholarcitedby'):
            self.fill()
        if hasattr(self, 'id_scholarcitedby'):
            soup = _get_soup(_SCHOLARPUB.format(requests.utils.quote(self.id_scholarcitedby)))
            return _search_scholar_soup(soup)
        else:
            return []

    def __str__(self):
        return pprint.pformat(self.__dict__)


class Author(object):
    """Returns an object for a single author"""
    def __init__(self, __data):
        if isinstance(__data, str):
            self.id = __data
        else:
            self.id = re.findall(_CITATIONAUTHRE, __data('a')[0]['href'])[0]
            self.url_picture = __data('img')[0]['src']
            self.name = __data.find('h3', class_='gsc_1usr_name').text
            affiliation = __data.find('div', class_='gsc_1usr_aff')
            if affiliation:
                self.affiliation = affiliation.text
            email = __data.find('div', class_='gsc_1usr_emlb')
            if email:
                self.email = email.text
            self.interests = [i.text.strip() for i in __data.findAll('a', class_='gsc_co_int')]
            citedby = __data.find('div', class_='gsc_1usr_cby')
            if citedby:
                self.citedby = int(citedby.text[9:])
        self._filled = False

    def fill(self):
        """Populate the Author with information from their profile"""
        url_citations = _CITATIONAUTH.format(self.id)
        soup = _get_soup('{0}&pagesize={1}'.format(url_citations, _PAGESIZE))
        self.name = soup.find('div', id='gsc_prf_in').text
        self.affiliation = soup.find('div', class_='gsc_prf_il').text
        self.interests = [i.text.strip() for i in soup.findAll('a', class_='gsc_prf_ila')]
        self.url_picture = soup.find('img')['src']

	#h-index, i10-index and h-index, i10-index in the last 5 years
        index = soup.findAll('td', class_='gsc_rsb_std')
        self.hindex = int(index[2].text)
        self.hindex5y = int(index[3].text)
        self.i10index = int(index[4].text)
        self.i10index5y = int(index[5].text)
        
        self.publications = list()
        pubstart = 0
        while True:
            for row in soup.findAll('tr', class_='gsc_a_tr'):
                new_pub = Publication(row, 'citations')
                self.publications.append(new_pub)
            if 'disabled' not in soup.find('button', id='gsc_bpf_next').attrs:
                pubstart += _PAGESIZE
                soup = _get_soup('{0}&cstart={1}&pagesize={2}'.format(url_citations, pubstart, _PAGESIZE))
            else:
                break
        self._filled = True
        return self

    def __str__(self):
        return pprint.pformat(self.__dict__)


def search_pubs_query(query):
    """Search by scholar query and return a generator of Publication objects"""
    soup = _get_soup(_PUBSEARCH.format(requests.utils.quote(query)))
    return _search_scholar_soup(soup)


def search_author(name):
    """Search by author name and return a generator of Author objects"""
    soup = _get_soup(_AUTHSEARCH.format(requests.utils.quote(name)))
    return _search_citation_soup(soup)


def search_keyword(keyword):
    """Search by keyword and return a generator of Author objects"""
    soup = _get_soup(_KEYWORDSEARCH.format(requests.utils.quote(keyword)))
    return _search_citation_soup(soup)


def search_pubs_custom_url(url):
    """Search by custom URL and return a generator of Publication objects
    URL should be of the form '/scholar?q=...'"""
    soup = _get_soup(url)
    return _search_scholar_soup(soup)


def search_author_custom_url(url):
    """Search by custom URL and return a generator of Publication objects
    URL should be of the form '/citation?q=...'"""
    soup = _get_soup(url)
    return _search_citation_soup(soup)


if __name__ == "__main__":
    author = next(search_author('Steven A. Cholewiak')).fill()
    print(author)
