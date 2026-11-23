import unittest
from notifications_zh import localized


class LocalizationTest(unittest.TestCase):
    def test_maintenance_item_names_and_due_status_are_translated(self):
        title, body = localized(dict(event='maintenance_due', printer='Lamps-X2D',
                                    items='- Lubricate Steel Rods (Soon)\n- Clean Carbon Rods (OVERDUE)\n- Custom task (Soon)'))
        self.assertEqual(title, '打印机维护提醒')
        self.assertIn('- 润滑钢杆（即将到期）', body)
        self.assertIn('- 清洁碳纤维杆（已到期）', body)
        self.assertIn('- Custom task（即将到期）', body)

    def test_queue_reasons_and_names_remain_separate(self):
        title, body = localized(dict(event='queue_job_waiting', job_name='Auto bed leveling.3mf',
                                    target_model='P2S', waiting_reason='Busy: Lamps-X2D | Offline: Lamps-P2S'))
        self.assertIn('任务：Auto bed leveling.3mf', body)
        self.assertIn('打印机忙碌：Lamps-X2D；打印机离线：Lamps-P2S', body)


if __name__ == '__main__':
    unittest.main()
