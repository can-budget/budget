"""Executable arithmetic checks only. API/concurrency/device tests are not mocked as passed."""
import copy
import json
import random
import unittest
from datetime import date, timedelta
from pathlib import Path
from budget_engine import project

ROOT = Path(__file__).parent

class BudgetTests(unittest.TestCase):
    def test_golden_cases(self):
        cases = json.loads((ROOT / 'golden_cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']):
                self.assertEqual(project(case['input']), case['expected'])

    def test_1000_randomized_invariant_scenarios(self):
        rng = random.Random(1618033)
        for trial in range(1000):
            n = rng.randint(1, 50)
            start = date(2027, 1, 1)
            end = start + timedelta(days=n-1)
            rows = [{'day': str(start+timedelta(days=i)), 'topups_minor': rng.randint(0, 50000),
                     'expenses_minor': rng.randint(0, 80000)} for i in range(n)]
            inp = dict(initial_minor=rng.randint(1, 1000000), start_date=str(start),
                       end_date=str(end), as_of_date=str(end), days=rows,
                       choices={row['day']: rng.choice(['today', 'distribute']) for row in rows[1:]})
            result = project(inp)
            expected = inp['initial_minor'] + sum(r['topups_minor']-r['expenses_minor'] for r in rows)
            self.assertEqual(result['bank_minor'], expected)
            self.assertEqual(result['today_remaining_minor'], expected)  # last day
            rng.shuffle(rows)
            self.assertEqual(project(inp), result)  # arrival order independence
            alt = copy.deepcopy(inp)
            alt['choices'] = {}
            self.assertEqual(project(alt)['bank_minor'], expected)  # carry cannot create bank money
            alt['as_of_date'] = str(end+timedelta(days=1))
            self.assertIsNone(project(alt)['today_remaining_minor'])
            self.assertEqual(project(alt)['bank_minor'], expected)

    def test_split_topups_independent_of_splitting(self):
        for amount in range(1, 100):
            inp = dict(initial_minor=10000,start_date='2026-10-11',end_date='2026-10-19',
                       as_of_date='2026-10-11',days=[dict(day='2026-10-11',topups_minor=amount)])
            expected=project(inp)
            inp['days']=[dict(day='2026-10-11',topups_minor=1)]*amount
            self.assertEqual(project(inp),expected)

    def test_invalid_inputs(self):
        valid=dict(initial_minor=100,start_date='2026-01-01',end_date='2026-01-10',as_of_date='2026-01-02')
        patches=[dict(initial_minor=0),dict(initial_minor=1.0),dict(initial_minor=True),
                 dict(initial_minor=1_000_000_000),dict(end_date='2027-01-02'),
                 dict(as_of_date='2025-12-31'),dict(start_date='2026-1-1'),
                 dict(days=[dict(day='2026-01-01',expenses_minor=-1)]),
                 dict(days=[dict(day='2026-01-01',expenses_minor=9_000_000_000_000)]),
                 dict(choices={'2026-01-02':'invent-money'})]
        for patch in patches:
            with self.subTest(patch=patch),self.assertRaises((ValueError,TypeError)):
                project(valid|patch)

    def test_full_leap_year_366_days(self):
        data=dict(initial_minor=36600,start_date='2028-01-01',end_date='2028-12-31',as_of_date='2028-01-01')
        self.assertEqual(project(data)['today_remaining_minor'],100)


class RevisionTests(unittest.TestCase):
    def test_500_revision_invariants(self):
        rng=random.Random(121026)
        # Vary date changes, signed deltas and late entries. Arithmetic only.
        for trial in range(500):
            initial=rng.randint(1,1000000)
            delta=rng.randint(-200000,200000)
            expense=rng.randint(0,300000)
            last=rng.randint(2,20)
            start=date(2026,10,1); end=start+timedelta(days=last-1)
            inp=dict(initial_minor=initial,start_date=str(start),end_date='2026-10-20',
                     as_of_date=str(end),days=[dict(day=str(start),expenses_minor=expense)],
                     revisions=[dict(effective_day='2026-10-02',sequence=1,bank_adjustment_minor=delta,end_date=str(end))])
            r=project(inp)
            self.assertEqual(r['bank_minor'],initial+delta-expense)
            self.assertEqual(r['today_remaining_minor'],r['bank_minor'])
            before=project(inp|{'as_of_date':str(start)})
            self.assertEqual(before['today_remaining_minor'],initial//20-expense)
            late=copy.deepcopy(inp);late['days'][0]['expenses_minor']+=17
            self.assertEqual(project(late)['bank_minor'],r['bank_minor']-17)

    def test_invalid_revisions(self):
        base=dict(initial_minor=10000,start_date='2026-10-11',end_date='2026-10-12',as_of_date='2026-10-11')
        candidates=[
            [dict(effective_day='2026-10-13',sequence=1,end_date='2026-10-15')],
            [dict(effective_day='2026-10-11',sequence=1,end_date='2026-10-10')],
            [dict(effective_day='2026-10-11',sequence=1,end_date='2027-10-12')],
            [dict(effective_day='2026-10-11',sequence=1,bank_adjustment_minor=True)],
            [dict(effective_day='2026-10-11',sequence=1,bank_adjustment_minor=1.1)],
            [dict(effective_day='2026-10-11',sequence=0,bank_adjustment_minor=1)],
            [dict(effective_day='2026-10-11',sequence=1),dict(effective_day='2026-10-11',sequence=1)],
            [dict(effective_day='2026-10-12',sequence=1),dict(effective_day='2026-10-11',sequence=2)],
        ]
        for rows in candidates:
            with self.subTest(rows=rows), self.assertRaises((ValueError,TypeError)):
                project(base|{'revisions':rows})

if __name__ == '__main__':
    unittest.main(verbosity=2)
