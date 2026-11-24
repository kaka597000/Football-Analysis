from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from publication import deliver
from research.analysis_freeze import digest, encoded, save
from research.chat_display_readback import match_readback, prepare
from research.display_delivery import attach_display, delivery_request


class ChatDisplayReadbackTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        self.sent = self.now + timedelta(minutes=1)
        self.deadline = self.now + timedelta(hours=1)
        self.room, self.author, self.message_id = 'test-room', 'test-author', 'test-message'
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.records = [self.freeze('401', 'eng.1'), self.freeze('402', 'ita.1')]
        self.blocks = [delivery_request(item)['text'] for item in self.records]
        self.body = '\n\n'.join(['HELLO BOSS，两场赛前推荐', *self.blocks])
        self.staged = self.stage(self.body)

    def freeze(self, id_, cohort):
        fixture = {'match_id': 'espn:' + id_, 'cohort': cohort, 'stage': 'regular',
                   'home': 'espn:1', 'away': 'espn:2', 'kickoff_at': self.deadline.isoformat(),
                   'cutoff_at': (self.now - timedelta(hours=1)).isoformat()}
        payload, _ = attach_display({'primary': '2-0', 'synthetic_test_only': True}, '2-0', ('2-1', '1-0'))
        with patch('research.analysis_freeze.datetime') as clock:
            clock.now.side_effect = self.now.astimezone
            clock.fromisoformat.side_effect = datetime.fromisoformat
            freeze = save(payload, fixture, stage='prematch', track='comprehensive', root=self.root)
        return json.loads(Path(freeze['path']).read_text(encoding='utf-8'))

    def stage(self, text, at=None):
        return prepare(self.records, text, room_id=self.room, author_id=self.author, at=at or self.now)

    def response(self, **changes):
        message = {'message_id': self.message_id, 'channel': 'chatgpt', 'author': 'aeon',
                   'author_id': self.author, 'content': {'text': self.body, 'library_attachments': []},
                   'sent_at': self.sent.isoformat(), 'deleted_at': None}
        return {'room_id': self.room, 'partial': False, 'message': dict(message, **changes)}

    def check(self, response):
        return match_readback(self.staged, response, message_id=self.message_id,
                              at=self.deadline + timedelta(hours=3))

    def test_named_provider_readback_binds_the_whole_message_and_both_freezes(self):
        evidence = self.check(self.response())
        self.assertEqual(evidence['status'], 'READBACK_MATCHED')
        self.assertFalse(evidence['first_visible_verified'])
        self.assertFalse(evidence['actual_kickoff_verified'])
        self.assertEqual(evidence['sent_at'], self.sent.isoformat())
        self.assertEqual([m['freeze_id'] for m in evidence['matches']], [r['freeze_id'] for r in self.records])
        for match, record in zip(evidence['matches'], self.records, strict=True):
            self.assertEqual(match['ordered_scores'], ['2-0', '2-1', '1-0'])
            self.assertEqual(match['request_sha256'], digest(delivery_request(record)))

    def test_freeze_blocks_must_be_intact_unique_and_in_batch_order(self):
        for text in (self.blocks[0], self.body + '\n' + self.blocks[0], '\n'.join(reversed(self.blocks)),
                     self.body.replace('备选一：2-1', '备选一：3-0')):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'frozen display block'):
                self.stage(text)
        with self.assertRaisesRegex(ValueError, 'planned kickoff'):
            self.stage(self.body, at=self.deadline)

    def test_missing_partial_wrong_author_wrong_id_deleted_changed_or_late_stay_unverified(self):
        cases = [dict(self.response(), partial=True), dict(self.response(), message=None),
                 dict(self.response(), room_id='other-room'), self.response(author_id='other'),
                 self.response(channel='email'), self.response(message_id='other-id'),
                 self.response(deleted_at=self.deadline.isoformat()),
                 self.response(content={'text': self.body.replace('2-0', '2-1')}),
                 self.response(sent_at=self.deadline.isoformat()),
                 self.response(sent_at=(self.now - timedelta(seconds=1)).isoformat())]
        for index, response in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.check(response)
        self.staged['plan']['room_id'] = 'modified'
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.check(self.response())

    def test_retry_keeps_the_original_observation_and_rejects_a_new_version(self):
        path = self.root / 'readback.json'
        original = encoded(self.check(self.response()))
        deliver(path, original)
        deliver(path, encoded(self.check(self.response())))
        new_time = self.sent + timedelta(seconds=1)
        with self.assertRaises(FileExistsError):
            deliver(path, encoded(self.check(self.response(sent_at=new_time.isoformat()))))
        self.assertEqual(path.read_bytes(), original)
