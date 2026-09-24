"""Small document field declarations; no corpus answers or page coordinates."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    aliases: tuple[str, ...]
    kind: str = 'text'
    multiline: bool = False
    required: bool = False


@dataclass(frozen=True)
class DocumentSpec:
    key: str
    label: str
    fields: tuple[FieldSpec, ...]
    boundary_labels: tuple[str, ...] = ()
    table_preferred: bool = False


TYPE_LABELS = {
    'business_license': '营业执照',
    'device_operation_license': '医疗器械经营许可证',
    'device_production_license': '医疗器械生产许可证',
    'device_registration': '医疗器械注册证',
    'device_operation_filing': '第二类医疗器械经营备案',
    'device_production_filing': '第一类医疗器械生产备案',
    'device_product_filing': '第一类医疗器械产品备案',
    'enterprise_authorization': '企业授权书',
    'disinfection_hygiene_license': '消毒产品生产企业卫生许可证',
    'medical_practice_license': '医疗机构执业许可证',
    'quality_management_certificate': '质量管理体系认证',
    'drug_operation_license': '药品经营许可证',
}


def F(key, label, aliases=(), kind='text', multiline=False, required=False):
    return FieldSpec(key, label, tuple(dict.fromkeys((label, *aliases))), kind, multiline, required)


PROFILES = {
    'business_license': DocumentSpec('business_license', TYPE_LABELS['business_license'], (
        F('enterprise_name', '企业名称', ('名称',), 'company', True, True),
        F('unified_social_credit_code', '统一社会信用代码', (), 'uscc', False, True),
        F('legal_representative', '法定代表人', (), 'person'),
        F('address', '住所', ('主要经营场所', '经营场所', '营业场所'), multiline=True),
        F('establishment_date', '成立日期', ('成立时间',), 'date'),
        F('business_scope', '经营范围', multiline=True),
        F('enterprise_type', '类型', ('公司类型',), multiline=True),
        F('registered_capital', '注册资本', ('注册资金',)),
        F('registration_authority', '登记机关', (), 'authority'),
    ), ('营业期限', '经营期限', '登记日期', '副本编号', '年报', '二维码', '核准日期')),
    'device_registration': DocumentSpec('device_registration', TYPE_LABELS['device_registration'], (
        F('registration_number', '注册证编号', ('注册证号', '医疗器械注册证编号'), 'registration_number', False, True),
        F('registrant_name', '注册人名称', ('注册人',), 'company', True, True),
        F('registrant_address', '注册人住所', ('注册人注册地址',), multiline=True),
        F('production_address', '生产地址', ('生产场所',), multiline=True),
        F('product_name', '产品名称', ('产品名称、型号、规格',), multiline=True, required=True),
        F('model_specification', '型号规格', ('型号、规格', '型号/规格', '规格型号', '型号或规格'), multiline=True),
        F('structure_composition', '结构及组成', ('结构及组成/主要组成成分', '主要组成成分', '结构及主要组成'), multiline=True),
        F('intended_use', '适用范围', ('适用范围/预期用途', '预期用途', '产品适用范围'), multiline=True),
        F('approval_date', '批准日期', (), 'date'),
        F('effective_date', '生效日期', (), 'date'),
        F('valid_until', '有效期至', ('有效期截止日期', '至'), 'date_end'),
        F('attachment', '附件', (), multiline=True),
        F('remarks', '备注', (), multiline=True),
    ), ('代理人名称', '代理人住所', '代理人注册地址', '境内代理人名称', '境内代理人住所', '产品技术要求编号', '生产企业名称', '生产企业住所', '其他内容'), True),
    'device_production_license': DocumentSpec('device_production_license', TYPE_LABELS['device_production_license'], (
        F('license_number', '许可证编号', ('许可证号', '编号'), 'license_number', False, True),
        F('enterprise_name', '企业名称', ('名称',), 'company', True, True),
        F('unified_social_credit_code', '统一社会信用代码', (), 'uscc'),
        F('legal_representative', '法定代表人', (), 'person'),
        F('enterprise_head', '企业负责人', ('负责人',), 'person'),
        F('address', '住所', ('注册地址', '企业注册地址'), multiline=True),
        F('production_address', '生产地址', ('生产场所',), multiline=True),
        F('production_scope', '生产范围', (), multiline=True),
        F('valid_from', '许可期限', ('有效期限', '自'), 'date_start'),
        F('valid_until', '有效期至', ('至',), 'date_end'),
        F('issue_date', '发证日期', ('签发日期',), 'date'),
        F('issuing_authority', '发证机关', ('发证部门', '发证机构'), 'authority'),
    ), ('日常监管机构', '日常监管部门', '监管机构', '监督电话', '投诉举报电话', '签发人', '生产产品登记表', '产品登记表', '变更记录', '登记事项变更记录')),
    'device_operation_license': DocumentSpec('device_operation_license', TYPE_LABELS['device_operation_license'], (
        F('license_number', '许可证编号', ('许可证号', '编号'), 'license_number', False, True),
        F('enterprise_name', '企业名称', ('名称',), 'company', True, True),
        F('unified_social_credit_code', '统一社会信用代码', (), 'uscc'),
        F('legal_representative', '法定代表人', (), 'person'),
        F('enterprise_head', '企业负责人', ('负责人',), 'person'),
        F('address', '住所', ('注册地址',), multiline=True),
        F('business_address', '经营场所', ('经营地址',), multiline=True),
        F('warehouse_address', '库房地址', ('仓库地址',), multiline=True),
        F('business_mode', '经营方式'),
        F('business_scope', '经营范围', (), multiline=True),
        F('valid_from', '许可期限', ('有效期限', '自'), 'date_start'),
        F('valid_until', '有效期至', ('至',), 'date_end'),
        F('issue_date', '发证日期', ('签发日期',), 'date'),
        F('issuing_authority', '发证机关', ('发证部门', '发证机构'), 'authority'),
    ), ('日常监督管理机构', '日常监管机构', '监督电话', '投诉举报电话', '签发人', '变更记录', '变更情况')),
}

# Additional documents reuse the same spatial/evidence parser.
def _shared(keys,profile='device_operation_license'):
    return tuple(f for f in PROFILES[profile].fields if f.key in keys)
def _doc(key,fields,boundaries=(),table=False):
    PROFILES[key]=DocumentSpec(key,TYPE_LABELS[key],tuple(fields),tuple(boundaries),table)
_doc('device_operation_filing',[
    F('filing_number','备案编号',('备案号',),'text',required=True),
    *_shared(['enterprise_name','unified_social_credit_code','legal_representative','enterprise_head','address','business_address','warehouse_address','business_mode','business_scope']),
    F('filing_date','备案日期',kind='date'),F('filing_authority','备案部门',('备案机关','备案单位'),kind='authority'),
    F('remarks','备注',multiline=True)
],['变更记录','变更情况','备案变更情况'],True)
_doc('device_production_filing',[
    F('filing_number','备案编号',('备案号',),'text',required=True),
    *_shared(['enterprise_name','unified_social_credit_code','legal_representative','enterprise_head','address','production_address','production_scope'],'device_production_license'),
    F('filing_date','备案日期',kind='date'),F('filing_authority','备案部门',('备案机关','备案单位'),kind='authority'),
    F('remarks','备注',multiline=True)
],['生产产品列表','生产产品','产品备案号','变更备案记录','变更记录','联系电话','邮编'],True)
_doc('device_product_filing',[
    F('filing_number','备案编号',('备案号',),'text',required=True),
    F('registrant_name','备案人名称',('备案人',),kind='company',multiline=True,required=True),
    F('organization_code','备案人组织机构代码',('统一社会信用代码','组织机构代码')),
    F('registrant_address','备案人注册地址',('备案人住所','注册地址'),multiline=True),
    F('production_address','生产地址',multiline=True),F('product_name','产品名称',multiline=True),
    F('model_specification','型号规格',('型号/规格','型号、规格','规格型号'),multiline=True),
    F('structure_composition','产品描述',('结构及组成',),multiline=True),
    F('intended_use','预期用途',('适用范围',),multiline=True),
    F('filing_date','备案日期',kind='date'),F('filing_authority','备案部门',('备案单位','备案单位和日期'),kind='authority'),
    F('remarks','备注',multiline=True)
],['变更情况','变更记录','产品分类名称','附件'],True)
_doc('drug_operation_license',[
    F('license_number','许可证编号',('许可证号','证号'),required=True),
    F('enterprise_name','企业名称',('企业名称（名称）','名称'),kind='company',multiline=True,required=True),
    F('unified_social_credit_code','统一社会信用代码',('社会信用代码',),kind='uscc'),
    F('legal_representative','法定代表人',('法定代表人（负责人）','法定代表人(负责人)'),kind='person'),
    F('enterprise_head','主要负责人',('企业负责人',),kind='person'),
    F('quality_head','质量负责人',kind='person'),
    F('business_address','经营地址',('经营场所',),multiline=True),
    F('warehouse_address','仓库地址',('库房地址',),multiline=True),
    F('business_mode','经营方式'),F('business_scope','经营范围',multiline=True),
    F('valid_until','有效期至',kind='date_end'),F('issue_date','发证日期',('签发日期',),kind='date'),
    F('issuing_authority','发证机关',kind='authority')
],['日常监督管理机构','投诉举报电话','签发人','变更记录'])
_doc('disinfection_hygiene_license',[
    F('license_number','许可证编号',('许可证号','证号'),required=True),
    F('enterprise_name','单位名称',('企业名称',),kind='company',multiline=True,required=True),
    F('legal_representative','法定代表人',('法定代表人（负责人）','法定代表人(负责人)'),kind='person'),
    F('address','注册地址',('住所',),multiline=True),F('production_address','生产地址',multiline=True),
    F('production_mode','生产方式'),F('production_items','生产项目',multiline=True),
    F('production_category','生产类别',multiline=True),
    F('valid_from','有效期限',('自',),kind='date_start'),F('valid_until','有效期至',('至',),kind='date_end'),
    F('issue_date','发证日期',('批准日期',),kind='date'),
    F('issuing_authority','发证机关',('批准机关','卫生行政机关'),kind='authority')
],['注意事项','中华人民共和国国家卫生健康委员会制'])
_doc('medical_practice_license',[
    F('registration_number','登记号',('登记号码',),required=True),
    F('institution_name','医疗机构名称',('机构名称','名称'),kind='company',multiline=True,required=True),
    F('address','地址',('执业地址',),multiline=True),F('legal_representative','法定代表人',kind='person'),
    F('principal','主要负责人',('负责人',),kind='person'),
    F('ownership','所有制形式'),F('service_target','服务对象'),
    F('medical_subjects','诊疗科目',multiline=True),F('bed_count','床位',('床位数',)),
    F('valid_from','有效期限',('有效期自',),kind='date_start'),F('valid_until','有效期至',('至',),kind='date_end'),
    F('issue_date','发证日期',kind='date'),F('issuing_authority','登记机关',('发证机关',),kind='authority')
],['校验记录','校验日期','注意事项'])
