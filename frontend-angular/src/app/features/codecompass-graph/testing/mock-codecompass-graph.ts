// Mock domain_graph_artifact.v1 payload with real CodeCompass node kinds and edge types.
// Use this as input to GraphAdapterService.fromDomainArtifact() in tests and storybook.

export const MOCK_DOMAIN_GRAPH_ARTIFACT = {
  schema: 'domain_graph_artifact.v1',
  source_kind: 'codecompass_graph',
  source_ref: 'mock-index-001',
  nodes: [
    // java_type nodes (5)
    { node_id: 'n-OrderService',    node_type: 'java_type',   attributes: { name: 'OrderService',    file: 'src/main/java/shop/OrderService.java',    content: 'Handles order lifecycle', record_id: 'r1' } },
    { node_id: 'n-PaymentService',  node_type: 'java_type',   attributes: { name: 'PaymentService',  file: 'src/main/java/shop/PaymentService.java',  content: 'Processes payments',      record_id: 'r2' } },
    { node_id: 'n-UserRepository',  node_type: 'java_type',   attributes: { name: 'UserRepository',  file: 'src/main/java/shop/UserRepository.java',  content: 'JPA user repository',     record_id: 'r3' } },
    { node_id: 'n-OrderRepository', node_type: 'java_type',   attributes: { name: 'OrderRepository', file: 'src/main/java/shop/OrderRepository.java', content: 'JPA order repository',    record_id: 'r4' } },
    { node_id: 'n-BaseService',     node_type: 'java_type',   attributes: { name: 'BaseService',     file: 'src/main/java/shop/BaseService.java',     content: 'Abstract base service',   record_id: 'r5' } },
    // java_method nodes (8)
    { node_id: 'm-placeOrder',      node_type: 'java_method', attributes: { name: 'placeOrder',      file: 'src/main/java/shop/OrderService.java',    content: 'Places a new order',              record_id: 'r6'  } },
    { node_id: 'm-cancelOrder',     node_type: 'java_method', attributes: { name: 'cancelOrder',     file: 'src/main/java/shop/OrderService.java',    content: 'Cancels an existing order',       record_id: 'r7'  } },
    { node_id: 'm-charge',          node_type: 'java_method', attributes: { name: 'charge',          file: 'src/main/java/shop/PaymentService.java',  content: 'Charges payment method',          record_id: 'r8'  } },
    { node_id: 'm-refund',          node_type: 'java_method', attributes: { name: 'refund',          file: 'src/main/java/shop/PaymentService.java',  content: 'Refunds a payment',               record_id: 'r9'  } },
    { node_id: 'm-findByEmail',     node_type: 'java_method', attributes: { name: 'findByEmail',     file: 'src/main/java/shop/UserRepository.java',  content: 'Find user by email address',      record_id: 'r10' } },
    { node_id: 'm-findByOrderId',   node_type: 'java_method', attributes: { name: 'findByOrderId',   file: 'src/main/java/shop/OrderRepository.java', content: 'Find order by ID',                record_id: 'r11' } },
    { node_id: 'm-validate',        node_type: 'java_method', attributes: { name: 'validate',        file: 'src/main/java/shop/BaseService.java',     content: 'Validates entity',                record_id: 'r12' } },
    { node_id: 'm-audit',           node_type: 'java_method', attributes: { name: 'audit',           file: 'src/main/java/shop/BaseService.java',     content: 'Writes audit log entry',          record_id: 'r13' } },
    // config nodes (4)
    { node_id: 'c-appYml',          node_type: 'config',      attributes: { name: 'application.yml', file: 'src/main/resources/application.yml',     content: 'Spring app config',               record_id: 'r14' } },
    { node_id: 'c-dbYml',           node_type: 'config',      attributes: { name: 'db.yml',           file: 'src/main/resources/db.yml',              content: 'Database datasource config',      record_id: 'r15' } },
    { node_id: 'c-securityYml',     node_type: 'config',      attributes: { name: 'security.yml',     file: 'src/main/resources/security.yml',        content: 'Security filter chain config',    record_id: 'r16' } },
    { node_id: 'c-appCtx',          node_type: 'config',      attributes: { name: 'ApplicationContext.xml', file: 'src/main/resources/ApplicationContext.xml', content: 'Spring XML context', record_id: 'r17' } },
    // xml_tag nodes (2)
    { node_id: 'x-datasource',      node_type: 'xml_tag',     attributes: { name: 'datasource',      file: 'src/main/resources/ApplicationContext.xml', content: '<bean id="dataSource">',       record_id: 'r18' } },
    { node_id: 'x-txManager',       node_type: 'xml_tag',     attributes: { name: 'txManager',       file: 'src/main/resources/ApplicationContext.xml', content: '<bean id="txManager">',        record_id: 'r19' } },
    // unknown node (1)
    { node_id: 'u-legacy',          node_type: 'unknown',     attributes: { name: 'LegacyAdapter',   file: 'src/legacy/LegacyAdapter.groovy',         content: 'Legacy groovy adapter',           record_id: 'r20' } },
  ],
  edges: [
    // declares_method (4)
    { source_id: 'n-OrderService',    target_id: 'm-placeOrder',    relation: 'declares_method',         attributes: { confidence: 1.0 } },
    { source_id: 'n-OrderService',    target_id: 'm-cancelOrder',   relation: 'declares_method',         attributes: { confidence: 1.0 } },
    { source_id: 'n-PaymentService',  target_id: 'm-charge',        relation: 'declares_method',         attributes: { confidence: 1.0 } },
    { source_id: 'n-PaymentService',  target_id: 'm-refund',        relation: 'declares_method',         attributes: { confidence: 1.0 } },
    // child_of_type (4)
    { source_id: 'm-validate',        target_id: 'n-BaseService',   relation: 'child_of_type',           attributes: { confidence: 1.0 } },
    { source_id: 'm-audit',           target_id: 'n-BaseService',   relation: 'child_of_type',           attributes: { confidence: 1.0 } },
    { source_id: 'm-findByEmail',     target_id: 'n-UserRepository', relation: 'child_of_type',          attributes: { confidence: 1.0 } },
    { source_id: 'm-findByOrderId',   target_id: 'n-OrderRepository', relation: 'child_of_type',         attributes: { confidence: 1.0 } },
    // extends (2)
    { source_id: 'n-OrderService',    target_id: 'n-BaseService',   relation: 'extends',                 attributes: { confidence: 1.0 } },
    { source_id: 'n-PaymentService',  target_id: 'n-BaseService',   relation: 'extends',                 attributes: { confidence: 1.0 } },
    // implements (1)
    { source_id: 'n-UserRepository',  target_id: 'n-BaseService',   relation: 'implements',              attributes: { confidence: 0.9 } },
    // injects_dependency (3)
    { source_id: 'n-OrderService',    target_id: 'n-PaymentService',  relation: 'injects_dependency',    attributes: { confidence: 0.95 } },
    { source_id: 'n-OrderService',    target_id: 'n-OrderRepository', relation: 'injects_dependency',    attributes: { confidence: 0.95 } },
    { source_id: 'n-PaymentService',  target_id: 'n-UserRepository',  relation: 'injects_dependency',    attributes: { confidence: 0.9  } },
    // calls_probable_target (4)
    { source_id: 'm-placeOrder',      target_id: 'm-charge',          relation: 'calls_probable_target', attributes: { confidence: 0.85 } },
    { source_id: 'm-placeOrder',      target_id: 'm-validate',        relation: 'calls_probable_target', attributes: { confidence: 0.9  } },
    { source_id: 'm-cancelOrder',     target_id: 'm-refund',          relation: 'calls_probable_target', attributes: { confidence: 0.8  } },
    { source_id: 'm-cancelOrder',     target_id: 'm-audit',           relation: 'calls_probable_target', attributes: { confidence: 0.9  } },
    // transactional_boundary (2)
    { source_id: 'n-OrderService',    target_id: 'c-appCtx',          relation: 'transactional_boundary', attributes: { confidence: 1.0 } },
    { source_id: 'n-PaymentService',  target_id: 'c-appCtx',          relation: 'transactional_boundary', attributes: { confidence: 1.0 } },
    // declares_bean (2)
    { source_id: 'c-appCtx',          target_id: 'x-datasource',      relation: 'declares_bean',          attributes: { confidence: 1.0 } },
    { source_id: 'c-appCtx',          target_id: 'x-txManager',       relation: 'declares_bean',          attributes: { confidence: 1.0 } },
    // jpa_relation (2)
    { source_id: 'n-OrderRepository', target_id: 'n-UserRepository',  relation: 'jpa_relation',           attributes: { confidence: 0.85 } },
    { source_id: 'x-datasource',      target_id: 'c-dbYml',           relation: 'jpa_relation',           attributes: { confidence: 0.8  } },
    // child_of_file (2)
    { source_id: 'x-datasource',      target_id: 'c-appCtx',          relation: 'child_of_file',          attributes: { confidence: 1.0 } },
    { source_id: 'x-txManager',       target_id: 'c-appCtx',          relation: 'child_of_file',          attributes: { confidence: 1.0 } },
    // field_type_uses (2)
    { source_id: 'n-OrderService',    target_id: 'n-UserRepository',  relation: 'field_type_uses',        attributes: { confidence: 0.8  } },
    { source_id: 'n-PaymentService',  target_id: 'c-securityYml',     relation: 'field_type_uses',        attributes: { confidence: 0.7  } },
    // related (2)
    { source_id: 'u-legacy',          target_id: 'n-OrderService',    relation: 'related',                attributes: { confidence: 0.5  } },
    { source_id: 'u-legacy',          target_id: 'n-PaymentService',  relation: 'related',                attributes: { confidence: 0.5  } },
  ],
  metadata: {
    knowledge_index_id: 'mock-index-001',
    node_count: 20,
    edge_count: 30,
  },
  warnings: [],
};
