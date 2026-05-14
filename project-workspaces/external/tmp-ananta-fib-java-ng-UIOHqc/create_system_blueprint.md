# create_system_blueprint

```json
{
  "architecture_proposal": "Suggest a layered, microservices-based architecture (e.g., Presentation -> Application -> Domain -> Infrastructure) to ensure loose coupling and scalability. Define clear API gateways for inter-service communication.",
  "core_roles": "Identify key personas (e.g., End User, System Administrator, Data Analyst). Detail their required permissions, access levels, and primary interactions with the system components.",
  "data_flows": "Map out key data entities (e.g., User Profile, Order, Product) and trace their flow through core modules. Define interaction points, expected data formats (JSON/XML), and transmission protocols (REST/Message Queues).",
  "module_boundaries": "Identify distinct, cohesive functional modules (e.g., User Management Service, Catalog Service, Order Processing Service). Each module must have defined inputs/outputs and clear ownership boundaries to prevent crosstalk.",
  "scope": "Define the high-level boundaries of the system (What is in scope? What is explicitly out of scope?). Establish the primary business objectives and the user groups this blueprint serves.",
  "security_assumptions": "Implement Zero Trust principles. Assume all communication (internal/external) is untrusted. Mandate OAuth 2.0/JWT for authentication and granular Role-Based Access Control (RBAC) checks at every service endpoint. Encryption must be mandated for data in transit (TLS 1.2+) and at rest (AES-256)."
}
```
