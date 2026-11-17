FROM grafana/grafana:13.1.0
USER root
RUN mkdir -p /opt/benchmark-plugins && chown grafana:root /opt/benchmark-plugins
USER grafana
RUN grafana cli --pluginsDir /opt/benchmark-plugins plugins install grafana-clickhouse-datasource 4.23.5 \
 && grafana cli --pluginsDir /opt/benchmark-plugins plugins install yesoreyeram-infinity-datasource 4.1.6
ENV GF_PATHS_PLUGINS=/opt/benchmark-plugins
