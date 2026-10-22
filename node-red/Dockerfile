FROM nodered/node-red:4.1

# Seed the named volume on its first creation. Later deployments keep editor changes.
COPY --chown=node-red:node-red flows.json /data/flows.json
