import argparse
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

REQUIRED = {
    'delayedCollapseUpdatesTheNativeHostingGeometryAfterTheFade',
    'aRealPopoverPinsExpansionAndClosingStartsAFreshGracePeriod',
    'reentryCancelsAnInFlightFadeBeforeItCanShrinkTheHost',
    'reducedMotionFitsCompactGeometryWithoutAFadeSuspension',
}


def verify_report(path: Path, output: Path | None = None) -> None:
    completed = set()
    if path.stat().st_size == 0 and output is not None:
        # CI's Swift 6.2 left --xunit-output empty for Swift Testing.
        text = re.sub(r'\x1b\[[0-9;]*m', '', output.read_text())
        if not re.search(r'^✔ Suite AppDelegateHostTests passed\b', text, re.M):
            raise ValueError('native host suite did not complete')
        completed.update(re.findall(r'^✔ Test (\w+)\(\) passed\b', text, re.M))
    else:
        for case in ET.parse(path).iter('testcase'):
            if case.get('classname', '').split('.')[-1] != 'AppDelegateHostTests':
                continue
            name = case.get('name', '').removesuffix('()')
            if any(case.find(outcome) is not None for outcome in ('failure', 'error', 'skipped')):
                raise ValueError(f'native host test did not pass: {name}')
            if case.get('time') is None:
                raise ValueError(f'native host test did not finish: {name}')
            completed.add(name)
    missing = REQUIRED - completed
    if missing:
        raise ValueError('native host tests did not complete: ' + ', '.join(sorted(missing)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        verify_report(args.report, args.output)
    except (OSError, ET.ParseError, ValueError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
    print('PASS: native host lifecycle tests completed')
