#!/usr/bin/env python3
"""Check release-model integrity and local links in the static landing."""

from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'docs'
MODELS = {
    'face_detection_yunet_2022mar.onnx': '50ef07f702a31741ca46a4c0d947773b64143b9362780237bf0d427d6c79bab7',
    'face_detection_yunet_2023mar.onnx': '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4',
    'pphumanseg/human_segmentation_pphumanseg_2023mar.onnx': '552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24',
}


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.source = source
        self.ids = set()
        self.links = []
        self.language = None
        self.canonical = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'html':
            self.language = values.get('lang')
        if values.get('id'):
            assert values['id'] not in self.ids, f'{self.source}: duplicate id {values["id"]}'
            self.ids.add(values['id'])
        if tag == 'link' and values.get('rel') == 'canonical':
            self.canonical = values.get('href')
        for attribute in ('href', 'src'):
            if values.get(attribute):
                self.links.append(values[attribute])


def local_target(source, link):
    url = urlsplit(link)
    if url.scheme or url.netloc:
        return None
    path = unquote(url.path)
    if path.startswith('/enquadra/'):
        target = SITE / path[len('/enquadra/'):]
    elif path.startswith('/'):
        target = SITE / path.lstrip('/')
    else:
        target = source.parent / path if path else source
    target = target.resolve()
    assert target.is_relative_to(SITE.resolve()), f'{source}: link outside site: {link}'
    if target.is_dir():
        target /= 'index.html'
    assert target.is_file(), f'{source}: missing link: {link}'
    return target, unquote(url.fragment)


def main():
    for name, checksum in MODELS.items():
        path = ROOT / 'brio_control/models' / name
        assert sha256(path.read_bytes()).hexdigest() == checksum, f'Model checksum mismatch: {name}'
    pages = {}
    for source in SITE.rglob('*.html'):
        page = Page(source)
        page.feed(source.read_text(encoding='utf-8'))
        pages[source.resolve()] = page
    assert len(pages) >= 2, 'PT and EN pages are required'
    for relative, language, canonical in (
        ('index.html', 'pt-BR', 'https://caiolombello.github.io/enquadra/'),
        ('en/index.html', 'en', 'https://caiolombello.github.io/enquadra/en/'),
    ):
        page = pages[(SITE / relative).resolve()]
        assert page.language == language, f'{relative}: language mismatch'
        assert page.canonical == canonical, f'{relative}: canonical mismatch'
    for source, page in pages.items():
        for link in page.links:
            result = local_target(source, link)
            if result and result[1] and result[0] in pages:
                assert result[1] in pages[result[0]].ids, f'{source}: missing anchor: {link}'
    for source in SITE.rglob('*.css'):
        for link in re.findall(r'url\(\s*[\'"]?([^\)\'"\s]+)', source.read_text()):
            local_target(source, link)
    print(f'{len(pages)} HTML pages, local assets/anchors and {len(MODELS)} pinned models OK')


if __name__ == '__main__':
    main()
