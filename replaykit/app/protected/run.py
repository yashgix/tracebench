"""Run the protected checks named by argv[2:] against the shop tree named by argv[1] and print the result as JSON."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, sys.argv[1])
here = Path(__file__).resolve().parent
sys.path.insert(1, str(here))
loader = unittest.defaultTestLoader
suite = unittest.TestSuite(loader.discover(str(here), pattern=name, top_level_dir=str(here)) for name in sys.argv[2:])
result = unittest.TestResult()
suite.run(result)
print(json.dumps({"run": result.testsRun, "failed": sorted(test.id() for test, _ in result.failures + result.errors)}))
