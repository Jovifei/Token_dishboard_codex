import tempfile, unittest
from pathlib import Path
from core import *
class CoreTests(unittest.TestCase):
 def test_insert_before_tables(self):
  source=b'# hello\n[projects.a]\ntrust_level="trusted"\n'
  out=propose(source,{'tool_output_token_limit':6000});self.assertIn(source,out);self.assertEqual(parse(out)['tool_output_token_limit'],6000)
 def test_preserve_unknown(self):
  source=b'model="custom"\nsecret="private"\n[unknown]\nx=[1,2]\n'
  out=propose(source,{'model_reasoning_effort':'low'});self.assertEqual(parse(out)['unknown'],{'x':[1,2]});self.assertIn(b'secret="private"',out)
 def test_replace_scalar(self):self.assertEqual(parse(propose(b'tool_output_token_limit=8000 # old\n',{'tool_output_token_limit':6000})),{'tool_output_token_limit':6000})
 def test_unicode_prompt(self):self.assertEqual(parse(propose(b'',{'compact_prompt':'保留\n"目标"'}))['compact_prompt'],'保留\n"目标"')
 def test_bom(self):self.assertEqual(parse(propose(b'\xef\xbb\xbfmodel="x"',{'tool_output_token_limit':1}))['model'],'x')
 def test_reject_multiline_target(self):
  with self.assertRaises(SafeError):propose(b'compact_prompt="""x\ny"""',{'compact_prompt':'new'})
 def test_reject_quoted_target(self):
  with self.assertRaises(SafeError):propose(b'"tool_output_token_limit"=5',{'tool_output_token_limit':6})
 def test_preserve_multiline_other(self):
  src=b'developer_instructions="""x\n[not a table]\ny"""\n'
  self.assertEqual(parse(propose(src,{'tool_output_token_limit':6}))['developer_instructions'],'x\n[not a table]\ny')
 def test_scope_collision_rejected(self):
  src=b'developer_instructions="""x\ntool_output_token_limit=5\ny"""\n'
  with self.assertRaises(SafeError):propose(src,{'tool_output_token_limit':6})
 def test_nested_same_key_preserved(self):
  src=b'[profiles.x]\ntool_output_token_limit=10\n';out=propose(src,{'tool_output_token_limit':6});self.assertEqual(parse(out)['profiles']['x']['tool_output_token_limit'],10)
 def test_reject_security_key(self):
  with self.assertRaises(SafeError):propose(b'',{'approval_policy':'never'})
 def test_reject_bad_values(self):
  for v in [0,-1,True,1.5,'5',10000001]:
   with self.assertRaises(SafeError):propose(b'',{'tool_output_token_limit':v})
 def test_reject_bad_effort(self):
  with self.assertRaises(SafeError):propose(b'',{'model_reasoning_effort':'automatic'})
 def test_reject_empty(self):
  with self.assertRaises(SafeError):propose(b'',{})
 def test_bad_toml(self):
  with self.assertRaises(SafeError):propose(b'x=[',{'tool_output_token_limit':1})
 def test_duplicate_toml(self):
  with self.assertRaises(SafeError):propose(b'x=1\nx=2',{'tool_output_token_limit':1})
 def test_apply_backup_restore(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'config.toml';original=b'model="x"\n';p.write_bytes(original);new=propose(original,{'tool_output_token_limit':6000});bak=write_checked(p,digest(original),new);self.assertEqual(bak.read_bytes(),original);self.assertEqual(p.read_bytes(),new)
   reverse=write_checked(p,digest(new),bak.read_bytes());self.assertEqual(p.read_bytes(),original);self.assertEqual(reverse.read_bytes(),new)
 def test_conflict_preserves_newer(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'config.toml';p.write_bytes(b'x=2')
   with self.assertRaises(SafeError):write_checked(p,digest(b'x=1'),b'x=3')
   self.assertEqual(p.read_bytes(),b'x=2')
 def test_symlink_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'real.toml';p.write_bytes(b'x=1');link=Path(d)/'config.toml';link.symlink_to(p)
   with self.assertRaises(SafeError):read_target(link)
 def test_no_change(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'config.toml';p.write_bytes(b'x=1')
   with self.assertRaises(SafeError):write_checked(p,digest(b'x=1'),b'x=1')
class ExportTests(unittest.TestCase):
 def test_never_export_config(self):
  with tempfile.TemporaryDirectory() as d:
   for name in ['config.toml','foo.config.toml','CONFIG.TOML']:
    with self.assertRaises(SafeError):export_new(Path(d)/name,'bad')
 def test_no_overwrite(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'file.md';p.write_text('keep')
   with self.assertRaises(SafeError):export_new(p,'bad')
   self.assertEqual(p.read_text(),'keep')
 def test_new_export(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'out.snippet.toml';export_new(p,'x=1');self.assertEqual(p.read_text(),'x=1')
 def test_changed_backup(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.bak';p.write_bytes(b'x=2')
   with self.assertRaises(SafeError):read_backup(p,digest(b'x=1'))
 def test_backup_symlink(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'real';p.write_bytes(b'x=1');q=Path(d)/'link';q.symlink_to(p)
   with self.assertRaises(SafeError):read_backup(q,digest(b'x=1'))
 def test_valid_backup(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.bak';p.write_bytes(b'x=1');self.assertEqual(read_backup(p,digest(b'x=1')),b'x=1')
if __name__=='__main__':unittest.main()

