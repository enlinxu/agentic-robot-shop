import unittest
from stock_change import stock_script,changed_between

class StockControlTests(unittest.TestCase):
    def test_stock_change_is_one_sku_and_zero_is_a_write(self):
        script=stock_script('RMC',0)
        self.assertIn('updateOne',script)
        self.assertIn('"instock": 0',script)
        self.assertIn('"sku": "RMC"',script)
        self.assertNotIn('delete',script)
        self.assertIn('findOne',stock_script('RMC'))

    def test_identifier_is_json_encoded(self):
        self.assertIn('"sku": "quote\\\"value"',stock_script('quote"value',48))

    def test_native_mcp_server_tools_are_not_double_counted(self):
        def tool(name,start,end,kind='INTERNAL'):
            return {'kind':kind,'start_ns':start,'end_ns':end,'attributes':{'gen_ai.operation.name':'execute_tool','gen_ai.tool.name':name,'gen_ai.agent.name':'native-agent'}}
        rows=[tool('search_products',1,2),tool('get_product',3,9),tool('get_product',3,9)]
        self.assertTrue(changed_between(rows+[tool('search_products',1,2,'SERVER'),tool('get_product',3,9,'SERVER')],5))
        self.assertFalse(changed_between(rows,1))
        self.assertFalse(changed_between(rows[:2],5))
