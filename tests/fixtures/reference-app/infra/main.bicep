// Synthetic reference application shared by the public and restricted profiles.
@allowed(['public', 'restricted'])
param profile string
param location string = resourceGroup().location
param name string = 'refapp'
param publicNetworkAccess string = profile == 'restricted' ? 'Disabled' : 'Enabled'
param requireAuthentication bool = profile == 'restricted'
param deployPrivateEndpoint bool = profile == 'restricted'
param enableDiagnostics bool = true
param enableSoftDelete bool = true
param minTlsVersion string = '1.2'
param subnetId string = ''

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: {
    retentionInDays: 30
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${name}-plan'
  location: location
  sku: {
    name: 'B1'
  }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: '${name}-web'
  location: location
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: publicNetworkAccess
    siteConfig: {
      minTlsVersion: minTlsVersion
      ftpsState: 'Disabled'
    }
  }
}

resource auth 'Microsoft.Web/sites/config@2023-12-01' = if (requireAuthentication) {
  parent: site
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (enableDiagnostics) {
  name: 'to-workspace'
  scope: site
  properties: {
    workspaceId: workspace.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

resource endpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (deployPrivateEndpoint) {
  name: '${name}-web-pe'
  location: location
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'web'
        properties: {
          privateLinkServiceId: site.id
          groupIds: ['sites']
        }
      }
    ]
  }
}

resource content 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${name}content'
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowSharedKeyAccess: false
    publicNetworkAccess: publicNetworkAccess
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: content
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: enableSoftDelete
      days: 7
    }
  }
}
