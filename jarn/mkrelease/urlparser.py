import re
import urlparse


class URLParser(object):
    """A minimal URL parser and splitter."""

    scheme_re = re.compile('^(\S+?)://')

    def get_scheme(self, url):
        match = self.scheme_re.match(url)
        if match is not None:
            return match.group(1)
        return ''

    def is_url(self, url):
        return bool(self.get_scheme(url))

    def split(self, url):
        # Split all URLs like http URLs
        scheme = self.get_scheme(url)
        if scheme:
            url = 'http%s' % url[len(scheme):]
            ignored, host, path, qs, frag = urlparse.urlsplit(url)
            user, host = self._split_host(host)
            return scheme, user, host, path, qs, frag
        return '', '', '', url, '', ''

    def _split_host(self, host):
        if '@' in host:
            return host.split('@', 1)
        return '', host

