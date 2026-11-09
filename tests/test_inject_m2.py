import json
import unittest

from replaykit.inject import Truth, inject, validate_fault
from replaykit.model import FAULT_TYPES

POOL_MESSAGE = 'connection pool exhausted (50/50 in use)'
NOISE_RULES = ('disk_usage_high', 'cert_expiring', 'cpu_throttled')


def scenario(duration=600, services=('api', 'db', 'cache'), calls=None):
    if calls is None:
        calls = {'api': ['db'], 'db': ['cache']}
    return {
        'id': 'scn-m2',
        'duration': duration,
        'services': list(services),
        'calls': calls,
        'events': [],
    }


def fault(**overrides):
    base = {'type': 'bad_deploy', 'service': 'db', 'start': 120}
    base.update(overrides)
    return base


def events_of(incident, kind, service=None):
    return [
        event
        for event in incident.events
        if event['kind'] == kind
        and (service is None or event['service'] == service)
    ]


def metric_map(incident, service, name):
    return {
        event['t']: event['attrs']['value']
        for event in incident.events
        if event['kind'] == 'metric'
        and event['service'] == service
        and event['attrs']['name'] == name
    }


def logs(incident, service, message):
    return [
        event
        for event in events_of(incident, 'log', service)
        if event['attrs']['message'] == message
    ]


def alerts(incident, service):
    return events_of(incident, 'alert', service)


def deploys(incident, service=None):
    return events_of(incident, 'deploy', service)


def shape(event):
    return (
        event['t'],
        event['kind'],
        event['service'],
        json.dumps(event['attrs'], sort_keys=True),
    )


def non_noise_shapes(incident):
    return [
        shape(event)
        for event in incident.events
        if event['id'] not in incident.truth.noise_ids
    ]


class PoolExhaustionTest(unittest.TestCase):
    def setUp(self):
        self.incident = inject(
            scenario(),
            fault(type='pool_exhaustion', service='db', start=120),
            1,
        )

    def test_fault_type_is_registered(self):
        self.assertEqual(
            FAULT_TYPES,
            (
                'bad_deploy',
                'config_error',
                'dependency_timeout',
                'memory_leak',
                'pool_exhaustion',
            ),
        )

    def test_target_latency_and_error_rate(self):
        latency = metric_map(self.incident, 'db', 'latency_ms')
        error = metric_map(self.incident, 'db', 'error_rate')
        self.assertTrue(80 <= latency[60] <= 120)
        for t in (120, 180, 240, 600):
            self.assertTrue(1500 <= latency[t] <= 3000, t)
        self.assertLessEqual(error[120], 0.02)
        for t in (180, 240, 600):
            self.assertTrue(0.1 <= error[t] <= 0.3, t)

    def test_pool_logs(self):
        found = logs(self.incident, 'db', POOL_MESSAGE)
        self.assertEqual([event['t'] for event in found], list(range(120, 601, 60)))
        self.assertTrue(all(event['attrs']['level'] == 'warn' for event in found))

    def test_alert(self):
        found = alerts(self.incident, 'db')
        self.assertEqual(len(found), 1)
        self.assertEqual(
            (found[0]['t'], found[0]['attrs']),
            (240, {'rule': 'latency_high', 'severity': 'page'}),
        )

    def test_callers_lag_by_one_sample(self):
        latency = metric_map(self.incident, 'api', 'latency_ms')
        error = metric_map(self.incident, 'api', 'error_rate')
        self.assertTrue(80 <= latency[120] <= 120)
        self.assertLessEqual(error[120], 0.02)
        for t in (180, 240, 600):
            self.assertTrue(800 <= latency[t] <= 1500, t)
            self.assertTrue(0.05 <= error[t] <= 0.15, t)

    def test_callee_unchanged(self):
        ranges = (
            ('latency_ms', 80, 120),
            ('error_rate', 0, 0.02),
            ('memory_mb', 400, 420),
        )
        for name, low, high in ranges:
            for value in metric_map(self.incident, 'cache', name).values():
                self.assertTrue(low <= value <= high, name)

    def test_injected_ids(self):
        injected = self.incident.truth.injected_ids
        self.assertEqual(len(injected), 43)
        for event in self.incident.events:
            if event['kind'] != 'metric':
                continue
            service = event['service']
            name = event['attrs']['name']
            t = event['t']
            if service == 'db' and name == 'latency_ms' and t >= 120:
                self.assertIn(event['id'], injected)
            elif service == 'db' and name == 'error_rate' and t >= 180:
                self.assertIn(event['id'], injected)
            elif service == 'api' and name in ('latency_ms', 'error_rate') and t >= 180:
                self.assertIn(event['id'], injected)
            elif service == 'cache':
                self.assertNotIn(event['id'], injected)
            elif service == 'db' and name == 'memory_mb':
                self.assertNotIn(event['id'], injected)
        for event in logs(self.incident, 'db', POOL_MESSAGE):
            self.assertIn(event['id'], injected)
        for event in alerts(self.incident, 'db'):
            self.assertIn(event['id'], injected)


class CascadeTest(unittest.TestCase):
    def test_dependency_timeout_cascade_hops(self):
        incident = inject(
            scenario(),
            fault(type='dependency_timeout', service='cache', start=120, cascade=True),
            3,
        )
        db_latency = metric_map(incident, 'db', 'latency_ms')
        api_latency = metric_map(incident, 'api', 'latency_ms')
        db_error = metric_map(incident, 'db', 'error_rate')
        api_error = metric_map(incident, 'api', 'error_rate')
        self.assertTrue(80 <= db_latency[60] <= 120)
        self.assertTrue(80 <= api_latency[60] <= 120)
        for t in (120, 180, 600):
            self.assertTrue(5000 <= db_latency[t] <= 8000, t)
            self.assertTrue(9000 <= api_latency[t] <= 12000, t)
            self.assertTrue(0.2 <= db_error[t] <= 0.4, t)
            self.assertTrue(0.2 <= api_error[t] <= 0.4, t)
        cache_latency = metric_map(incident, 'cache', 'latency_ms')
        self.assertTrue(2000 <= cache_latency[120] <= 5000)

    def test_dependency_timeout_without_cascade_only_affects_direct_callers(self):
        incident = inject(
            scenario(),
            fault(type='dependency_timeout', service='cache', start=120),
            3,
        )
        db_latency = metric_map(incident, 'db', 'latency_ms')
        db_error = metric_map(incident, 'db', 'error_rate')
        api_latency = metric_map(incident, 'api', 'latency_ms')
        self.assertTrue(80 <= db_latency[120] <= 120)
        for t in (180, 600):
            self.assertTrue(800 <= db_latency[t] <= 1500, t)
            self.assertTrue(0.05 <= db_error[t] <= 0.15, t)
        for t in (120, 180, 600):
            self.assertTrue(80 <= api_latency[t] <= 120, t)
        self.assertLessEqual(max(metric_map(incident, 'api', 'error_rate').values()), 0.02)

    def test_pool_exhaustion_cascade(self):
        chain = scenario(
            services=('web', 'api', 'db'),
            calls={'web': ['api'], 'api': ['db']},
        )
        incident = inject(
            chain,
            fault(type='pool_exhaustion', service='db', start=120, cascade=True),
            5,
        )
        api_latency = metric_map(incident, 'api', 'latency_ms')
        web_latency = metric_map(incident, 'web', 'latency_ms')
        api_error = metric_map(incident, 'api', 'error_rate')
        web_error = metric_map(incident, 'web', 'error_rate')
        db_latency = metric_map(incident, 'db', 'latency_ms')
        db_error = metric_map(incident, 'db', 'error_rate')
        self.assertTrue(5000 <= api_latency[120] <= 8000)
        self.assertTrue(9000 <= web_latency[120] <= 12000)
        self.assertTrue(0.2 <= api_error[120] <= 0.4)
        self.assertTrue(0.2 <= web_error[120] <= 0.4)
        self.assertTrue(1500 <= db_latency[120] <= 3000)
        self.assertLessEqual(db_error[120], 0.02)
        self.assertTrue(0.1 <= db_error[180] <= 0.3)


class FaultValidationTest(unittest.TestCase):
    def test_accepts_optional_keys(self):
        validate_fault(
            scenario(),
            fault(
                type='pool_exhaustion',
                cascade=True,
                noise={'decoy_deploy': True, 'flapping_alerts': 3},
            ),
        )
        validate_fault(
            scenario(),
            fault(type='dependency_timeout', service='cache', cascade=False, noise={}),
        )

    def test_rejects_extra_and_missing_keys(self):
        missing = fault()
        del missing['start']
        with self.assertRaises(ValueError):
            validate_fault(scenario(), missing)
        with self.assertRaises(ValueError):
            validate_fault(scenario(), fault(extra=1))

    def test_rejects_bad_cascade(self):
        for value in (1, 0, 'true', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_fault(scenario(), fault(cascade=value))

    def test_rejects_cascade_for_ineligible_types(self):
        for type_ in ('bad_deploy', 'config_error', 'memory_leak'):
            with self.subTest(type=type_):
                with self.assertRaises(ValueError):
                    validate_fault(scenario(), fault(type=type_, cascade=True))

    def test_rejects_bad_noise_shape(self):
        for value in ([], True, 'noise', 3):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_fault(scenario(), fault(noise=value))

    def test_rejects_unknown_noise_keys(self):
        with self.assertRaises(ValueError):
            validate_fault(scenario(), fault(noise={'other': True}))

    def test_rejects_bad_noise_values(self):
        for noise in (
            {'decoy_deploy': 1},
            {'decoy_deploy': 'yes'},
            {'flapping_alerts': -1},
            {'flapping_alerts': 21},
            {'flapping_alerts': True},
            {'flapping_alerts': 1.5},
            {'flapping_alerts': '2'},
        ):
            with self.subTest(noise=noise):
                with self.assertRaises(ValueError):
                    validate_fault(scenario(), fault(noise=noise))

    def test_rejects_noise_on_single_service_scenario(self):
        single = scenario(services=('only',), calls={})
        for noise in ({'decoy_deploy': True}, {'flapping_alerts': 1}):
            with self.subTest(noise=noise):
                with self.assertRaises(ValueError):
                    validate_fault(single, fault(service='only', noise=noise))


class NoiseDecoyTest(unittest.TestCase):
    def test_decoy_deploy(self):
        incident = inject(
            scenario(),
            fault(
                type='bad_deploy',
                service='db',
                start=300,
                noise={'decoy_deploy': True},
            ),
            2,
        )
        decoy = incident.truth.decoy_service
        self.assertIsNotNone(decoy)
        self.assertNotEqual(decoy, 'db')
        self.assertIn(decoy, {'api', 'cache'})
        found = deploys(incident, decoy)
        self.assertEqual(len(found), 1)
        event = found[0]
        self.assertEqual(event['t'], 180)
        self.assertRegex(event['attrs']['version'], r'^r[0-9a-f]{6}$')
        self.assertIn(event['id'], incident.truth.noise_ids)
        self.assertNotIn(event['id'], incident.truth.injected_ids)
        self.assertEqual(len(incident.truth.noise_ids), 1)
        self.assertEqual([e['t'] for e in deploys(incident, 'db')], [300])

    def test_decoy_time_is_clamped_to_zero(self):
        incident = inject(
            scenario(),
            fault(
                type='config_error',
                service='db',
                start=60,
                noise={'decoy_deploy': True},
            ),
            1,
        )
        event = deploys(incident, incident.truth.decoy_service)[0]
        self.assertEqual(event['t'], 0)


class NoiseFlappingTest(unittest.TestCase):
    def test_flapping_alerts(self):
        incident = inject(
            scenario(),
            fault(
                type='bad_deploy',
                service='db',
                start=120,
                noise={'flapping_alerts': 5},
            ),
            4,
        )
        ticket = [
            event
            for event in events_of(incident, 'alert')
            if event['attrs']['severity'] == 'ticket'
        ]
        self.assertEqual(len(ticket), 5)
        self.assertEqual(len(incident.truth.noise_ids), 5)
        samples = set(range(0, 601, 60))
        for event in ticket:
            self.assertIn(event['service'], {'api', 'cache'})
            self.assertIn(event['attrs']['rule'], NOISE_RULES)
            self.assertIn(event['t'], samples)
            self.assertIn(event['id'], incident.truth.noise_ids)
            self.assertNotIn(event['id'], incident.truth.injected_ids)


class NoiseDeterminismTest(unittest.TestCase):
    def test_same_seed_same_noise(self):
        spec = fault(
            type='pool_exhaustion',
            service='db',
            start=120,
            cascade=True,
            noise={'decoy_deploy': True, 'flapping_alerts': 3},
        )
        first = inject(scenario(), spec, 7)
        second = inject(scenario(), spec, 7)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.truth, second.truth)

    def test_noise_does_not_change_non_noise_events(self):
        base = fault(type='dependency_timeout', service='db', start=120)
        plain = inject(scenario(), base, 9)
        noisy = inject(
            scenario(),
            {**base, 'noise': {'decoy_deploy': True, 'flapping_alerts': 4}},
            9,
        )
        self.assertEqual(non_noise_shapes(plain), non_noise_shapes(noisy))
        self.assertEqual(
            len(plain.events),
            len(noisy.events) - len(noisy.truth.noise_ids),
        )


class TruthDefaultsTest(unittest.TestCase):
    def test_four_field_truth_keeps_working(self):
        truth = Truth('db', 'bad_deploy', 120, frozenset({'ev-0001'}))
        self.assertEqual(truth.noise_ids, frozenset())
        self.assertIsNone(truth.decoy_service)

    def test_incident_without_noise_has_empty_noise_fields(self):
        incident = inject(scenario(), fault(service='db'), 1)
        self.assertEqual(incident.truth.noise_ids, frozenset())
        self.assertIsNone(incident.truth.decoy_service)


if __name__ == '__main__':
    unittest.main()
