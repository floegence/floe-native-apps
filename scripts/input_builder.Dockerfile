ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG QT_MAJOR
COPY scripts/input_builder.sh /tmp/input_builder.sh
# Certificate roots are fixture build inputs. APT still verifies the original
# Debian archive signatures and package checksums against the fixed snapshot.
COPY input_modules/certificates/ /etc/ssl/certs/
RUN touch /tmp/floe-input-build-fixture && sh /tmp/input_builder.sh "$QT_MAJOR"
