// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org

import { Field, PanelSectionRow } from "@decky/ui";
import { FC } from "react";
import { FaInfoCircle } from "react-icons/fa";

import { Collapsible } from "./Collapsible";
import { theme } from "../lib/theme";

interface AboutSectionProps {
  version: string;
  tracked: number;
  signedOut: boolean;
  deviceName?: string | null;
}

export const AboutSection: FC<AboutSectionProps> = ({
  version, tracked, signedOut, deviceName,
}) => (
  <Collapsible title="About" icon={<FaInfoCircle size={14} />}>
    <PanelSectionRow>
      <Field label="Version" bottomSeparator="none">
        {version || "unknown"}
      </Field>
    </PanelSectionRow>
    <PanelSectionRow>
      <Field label="Games synced" bottomSeparator="none">{tracked}</Field>
    </PanelSectionRow>
    <PanelSectionRow>
      <Field label="Signed in" bottomSeparator="none">
        <span style={{ color: signedOut ? theme.bad : theme.good }}>
          {signedOut ? "No" : "Yes"}
        </span>
      </Field>
    </PanelSectionRow>
    {deviceName && (
      <PanelSectionRow>
        <Field label="This device" bottomSeparator="none">
          {deviceName}
        </Field>
      </PanelSectionRow>
    )}
  </Collapsible>
);
