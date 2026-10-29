targetScope = 'resourceGroup'

@sealed()
type modelDeployment = {
  @minLength(2)
  @maxLength(64)
  deploymentName: string
  @minLength(1)
  modelName: string
  @minLength(1)
  modelVersion: string
  sku: 'Standard' | 'GlobalStandard' | 'DataZoneStandard'
  @minValue(1)
  capacity: int
}

@description('Choose a region after checking model/version/SKU availability and subscription quota.')
param location string

@description('Globally unique account name, also used as its Entra-compatible custom subdomain.')
@minLength(2)
@maxLength(64)
param accountName string = 'ekb-${uniqueString(resourceGroup().id)}'

@description('Create a lightweight Foundry project, without agent compute or capability hosts.')
param createProject bool = true

@minLength(2)
@maxLength(64)
param projectName string = 'ekb'

@description('Required explicit student model/version/SKU/capacity, verified against regional quota and lifecycle.')
param student modelDeployment

@description('Small PAYG embedding deployment. Capacity is SKU/model-specific quota units, not a budget.')
param embedding modelDeployment = {
  deploymentName: 'ekb-embedding'
  modelName: 'text-embedding-3-small'
  modelVersion: '1'
  sku: 'Standard'
  capacity: 1
}

@description('OFF by default. Supply an explicit verified OpenAI model/version/SKU for teacher calls.')
param teacher modelDeployment?

@description('Optional Entra object ID (not application/client ID) for account-scoped inference RBAC.')
param operatorPrincipalId string = ''

@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param operatorPrincipalType string = 'User'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: createProject
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (createProject) {
  parent: account
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'EKB model-only foundation; no managed agent runtime.'
  }
}

// Account deployment writes are serialized to avoid concurrent account-update conflicts.
resource studentDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: student.deploymentName
  sku: {
    name: student.sku
    capacity: student.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: student.modelName
      version: student.modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: embedding.deploymentName
  dependsOn: [
    studentDeployment
  ]
  sku: {
    name: embedding.sku
    capacity: embedding.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embedding.modelName
      version: embedding.modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource teacherDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (teacher != null) {
  parent: account
  name: teacher!.deploymentName
  dependsOn: [
    embeddingDeployment
  ]
  sku: {
    name: teacher!.sku
    capacity: teacher!.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: teacher!.modelName
      version: teacher!.modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

var openAIUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
)

resource operatorInferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(operatorPrincipalId)) {
  name: guid(account.id, operatorPrincipalId, openAIUserRoleId)
  scope: account
  properties: {
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
    roleDefinitionId: openAIUserRoleId
  }
}

output accountId string = account.id
output projectId string = createProject ? project!.id : ''
output teacherDeploymentName string = teacher != null ? teacher!.deploymentName : ''
output ekbEnvironment object = {
  EKB_INFERENCE: 'openai'
  EKB_MODEL_ENDPOINT: uri(account.properties.endpoints['OpenAI Language Model Instance API'], 'openai/v1/')
  EKB_MODEL_ID: studentDeployment.name
  EKB_RECONCILIATION_MODEL_ID: studentDeployment.name
  EKB_EMBEDDING_MODEL_ID: embeddingDeployment.name
  EKB_MODEL_MANAGED_IDENTITY: 'true'
  EKB_MODEL_STRUCTURED_OUTPUT: 'true'
}
