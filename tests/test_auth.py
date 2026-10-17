import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth

class AuthTest(unittest.TestCase):
    def test_callback_fixed(self):
        self.assertEqual(auth.CALLBACK, 'http://localhost:8765/callback')

if __name__ == '__main__':
    unittest.main()
