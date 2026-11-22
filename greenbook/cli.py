"""Read a complete synthetic evidence bundle without external services."""
import argparse
import sqlite3
import sys
from pathlib import Path

from .archive import history, kind, load, publish
from .common import decode, encode, require
from .receipt import canonical
from .resign import plan
from .workflow import DESCRIPTION, materialize


def _bundle(path):
    doc = decode(Path(path).read_text(encoding='utf-8'))
    require(isinstance(doc, dict), 'bundle must be an object')
    return doc


def _replay_plan(stored_source):
    base_doc, base_result = stored_source['base_source'], stored_source['base_result']
    require(encode(materialize(base_doc)) == encode(base_result),
            'stored base evidence does not replay')
    receipt = canonical(stored_source['receipt'], base_result, stored_source['base_edition'])
    candidate = stored_source['candidate_bundle']
    return plan(base_doc, base_result, candidate, materialize(candidate), receipt,
                stored_source['base_edition'], stored_source.get('candidate_edition'))


def _resign(args):
    base_doc = load(args.archive, args.base, source=True)
    base_result = load(args.archive, args.base)
    require(kind(args.archive, args.base) == 'edition', f'not an evidence edition: {args.base}')
    require(encode(materialize(base_doc)) == encode(base_result),
            f'base edition does not replay: {args.base}')
    candidate_doc = _bundle(args.candidate)
    candidate_result = materialize(candidate_doc)
    receipt = canonical(decode(Path(args.receipt).read_text(encoding='utf-8')),
                        base_result, args.base)
    result = plan(base_doc, base_result, candidate_doc, candidate_result, receipt,
                  args.base, args.edition)
    source = {'kind': 'resign_plan', 'base_edition': args.base,
              'candidate_edition': args.edition,
              'base_source': base_doc, 'base_result': base_result,
              'candidate_bundle': candidate_doc, 'receipt': receipt}
    return source, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('preview', help='compute results without writing an archive').add_argument('bundle', type=Path)
    rel = subs.add_parser('publish', help='commit one immutable edition')
    rel.add_argument('bundle', type=Path)
    rel.add_argument('archive', type=Path)
    rel.add_argument('edition')
    rsg = subs.add_parser('resign', help='plan a certificate-reissue re-signing against a receipt')
    rsg.add_argument('archive', type=Path)
    rsg.add_argument('base')
    rsg.add_argument('candidate', type=Path)
    rsg.add_argument('receipt', type=Path)
    rsg.add_argument('edition')
    for command in ('show', 'source', 'replay'):
        p = subs.add_parser(command)
        p.add_argument('archive', type=Path)
        p.add_argument('edition')
    subs.add_parser('history').add_argument('archive', type=Path)
    args = parser.parse_args(argv)
    try:
        match args.command:
            case 'preview' | 'publish':
                doc = _bundle(args.bundle)
                result = materialize(doc)
                if args.command == 'publish':
                    result = publish(args.archive, args.edition, doc, result)
            case 'resign':
                source, result = _resign(args)
                result = publish(args.archive, args.edition, source, result, kind='resign_plan')
            case 'history':
                result = history(args.archive)
            case 'replay':
                stored = load(args.archive, args.edition)
                stored_source = load(args.archive, args.edition, source=True)
                if kind(args.archive, args.edition) == 'resign_plan':
                    result = _replay_plan(stored_source)
                else:
                    result = materialize(stored_source)
                require(encode(stored) == encode(result), f'replay differs: {args.edition}')
            case _:
                result = load(args.archive, args.edition, source=args.command == 'source')
        print(encode(result))
        return 0
    except (OSError, ValueError, LookupError, TypeError, ArithmeticError, sqlite3.Error) as exc:
        print(f'{parser.prog}: {exc}', file=sys.stderr)
        return 2
