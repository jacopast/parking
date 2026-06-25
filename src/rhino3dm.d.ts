declare module 'rhino3dm/rhino3dm.module.js' {
  export type RhinoFactory = () => Promise<any>;

  const rhino3dm: RhinoFactory;
  export default rhino3dm;
}
