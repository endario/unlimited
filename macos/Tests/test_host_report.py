import pathlib
import runpy
import tempfile
import unittest
import xml.etree.ElementTree as ET

verify_report = runpy.run_path(str(pathlib.Path(__file__).with_name('verify-host-tests.py')))['verify_report']

HOST_CASES = (
    'delayedCollapseUpdatesTheNativeHostingGeometryAfterTheFade()',
    'aRealPopoverPinsExpansionAndClosingStartsAFreshGracePeriod()',
    'reentryCancelsAnInFlightFadeBeforeItCanShrinkTheHost()',
    'reducedMotionFitsCompactGeometryWithoutAFadeSuspension()',
)


class HostReportTests(unittest.TestCase):
    def report(self, names=HOST_CASES, outcome=None, unfinished=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = pathlib.Path(directory.name) / 'report.xml'
        root = ET.Element('testsuites')
        suite = ET.SubElement(root, 'testsuite')
        ET.SubElement(suite, 'testcase', classname='UnlimitedKitTests', name='kitPassed()')
        for name in names:
            case = ET.SubElement(suite, 'testcase', classname='UnlimitedTests.AppDelegateHostTests', name=name)
            if name != unfinished:
                case.set('time', '0.01')
            if outcome:
                ET.SubElement(case, outcome)
        ET.ElementTree(root).write(path)
        return path

    def test_completed_native_cases_pass(self):
        verify_report(self.report())

    def test_other_target_success_does_not_mask_an_early_host_exit(self):
        with self.assertRaises(ValueError):
            verify_report(self.report(names=()))

    def test_each_required_native_case_must_complete(self):
        for missing in HOST_CASES:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                verify_report(self.report(names=tuple(name for name in HOST_CASES if name != missing)))

    def test_started_but_unfinished_native_cases_are_not_green(self):
        for name in HOST_CASES:
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_report(self.report(unfinished=name))

    def test_legacy_swift_output_requires_each_passed_host_case(self):
        for missing in (None, *HOST_CASES):
            with self.subTest(missing=missing):
                report = self.report()
                report.write_text('')
                output = report.with_name('output.log')
                names = tuple(name for name in HOST_CASES if name != missing)
                output.write_text('\n'.join(f'✔ Test {name} passed after 0.1 seconds.' for name in names)
                                  + '\n✔ Suite AppDelegateHostTests passed after 1 seconds.\n')
                if missing is None:
                    verify_report(report, output)
                else:
                    with self.assertRaises(ValueError):
                        verify_report(report, output)

    def test_legacy_output_requires_suite_completion(self):
        report = self.report()
        report.write_text('')
        output = report.with_name('output.log')
        output.write_text('\n'.join(f'✔ Test {name} passed after 0.1 seconds.' for name in HOST_CASES))
        with self.assertRaises(ValueError):
            verify_report(report, output)

    def test_native_failures_errors_and_skips_are_not_green(self):
        for outcome in ('failure', 'error', 'skipped'):
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                verify_report(self.report(outcome=outcome))


if __name__ == '__main__':
    unittest.main()
