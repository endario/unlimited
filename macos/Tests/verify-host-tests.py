import argparse
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

REQUIRED = {
    'delayedCollapseUpdatesTheNativeHostingGeometryAfterTheFade',
    'aRealPopoverPinsExpansionAndClosingStartsAFreshGracePeriod',
    'reentryCancelsAnInFlightFadeBeforeItCanShrinkTheHost',
}


def verify_report(path: Path) -> None:
    completed = set()
    for case in ET.parse(path).iter('testcase'):
        if case.get('classname', '').split('.')[-1] != 'AppDelegateHostTests':
            continue
        name = case.get('name', '').removesuffix('()')
        if any(case.find(outcome) is not None for outcome in ('failure', 'error', 'skipped')):
            raise ValueError(f'native host test did not pass: {name}')
        completed.add(name)
    missing = REQUIRED - completed
    if missing:
        raise ValueError('native host tests did not complete: ' + ', '.join(sorted(missing)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    try:
        verify_report(args.report)
    except (OSError, ET.ParseError, ValueError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
    print('PASS: native host lifecycle tests completed')
