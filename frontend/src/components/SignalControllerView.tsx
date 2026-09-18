import React from 'react';
import { Camera } from '../types/traffic';
import { SignalWidget } from './SignalWidget';
// @ts-ignore
import JunctionSimulator from './JunctionSimulator';

interface SignalControllerViewProps {
  cameras: Camera[];
  selectedCamera: Camera | null;
  onSelectCamera: (camera: Camera) => void;
}

export const SignalControllerView: React.FC<SignalControllerViewProps> = ({
  cameras,
  selectedCamera,
  onSelectCamera,
}) => {
  return (
    <div className="space-y-6">
      <SignalWidget
        cameras={cameras}
        selectedCamera={selectedCamera}
        onSelectCamera={onSelectCamera}
      />
      <JunctionSimulator />
    </div>
  );
};

export default SignalControllerView;
